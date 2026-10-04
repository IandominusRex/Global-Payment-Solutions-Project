-- Star schema: facts (the timestamped flow). All timestamps are UTC.
-- The answer key (anomaly labels, true invoice allocations) is NOT here:
-- it lives in data/answer_key/ so detection and matching code cannot peek at it.

CREATE TABLE IF NOT EXISTS fact_fx_rate (
    date_id              INTEGER NOT NULL REFERENCES dim_date(date_id),
    currency_code        TEXT    NOT NULL REFERENCES dim_currency(currency_code),
    rate_to_sgd          NUMERIC NOT NULL,
    is_published_fixing  BOOLEAN NOT NULL,   -- false = weekend carry-forward
    PRIMARY KEY (date_id, currency_code)
);

-- [+] Invoices exist before cash moves: basis for the direct forecast (5) and reconciliation (8).
CREATE TABLE IF NOT EXISTS fact_invoice (
    invoice_id        TEXT PRIMARY KEY,
    direction         TEXT NOT NULL CHECK (direction IN ('AR','AP')),
    entity_id         TEXT NOT NULL REFERENCES dim_entity(entity_id),
    counterparty_id   TEXT NOT NULL REFERENCES dim_counterparty(counterparty_id),
    invoice_ref       TEXT NOT NULL,
    currency_code     TEXT NOT NULL REFERENCES dim_currency(currency_code),
    amount            NUMERIC NOT NULL,
    issue_date_id     INTEGER NOT NULL REFERENCES dim_date(date_id),
    due_date_id       INTEGER NOT NULL REFERENCES dim_date(date_id),
    status            TEXT NOT NULL      -- open / paid / part_paid / written_off
);

-- Current state of each payment (upserted). History is in fact_payment_event.
CREATE TABLE IF NOT EXISTS fact_payment (
    payment_id        TEXT PRIMARY KEY,
    end_to_end_id     TEXT NOT NULL,                 -- [+] pain.001 EndToEndId
    uetr              TEXT,                          -- [+] gpi tracker id (cross-border)
    batch_id          TEXT,                          -- [+] file/batch it was submitted in
    direction         TEXT NOT NULL CHECK (direction IN ('OUT','IN')),  -- [+]
    account_id        TEXT NOT NULL REFERENCES dim_account(account_id), -- blueprint payer_account_id; our side for both directions
    counterparty_id   TEXT NOT NULL REFERENCES dim_counterparty(counterparty_id),
    type_id           INTEGER NOT NULL REFERENCES dim_payment_type(type_id),
    channel           TEXT NOT NULL,                 -- [+] API / H2H_FILE / PORTAL / LEGACY_FILE, INBOUND for receipts
    sender_country    TEXT NOT NULL,                 -- [+] corridor = sender_country -> receiver_country
    receiver_country  TEXT NOT NULL,                 -- [+]
    amount            NUMERIC NOT NULL,
    currency_code     TEXT NOT NULL REFERENCES dim_currency(currency_code),
    amount_sgd        NUMERIC NOT NULL,              -- converted at the value-date fixing
    fx_rate_applied   NUMERIC,                       -- [+] incl. bank spread, when a conversion happened
    charge_bearer     TEXT CHECK (charge_bearer IN ('OUR','SHA','BEN')),  -- [+]
    fees              NUMERIC NOT NULL DEFAULT 0,    -- [+]
    amount_received   NUMERIC,                       -- [+] after intermediary deductions
    purpose_code      TEXT REFERENCES dim_purpose_code(purpose_code),
    remittance_ref    TEXT,                          -- [+] quality varies by payer (analysis 8)
    is_intercompany   BOOLEAN NOT NULL,              -- [+]
    initiated_ts      TIMESTAMP NOT NULL,
    submitted_ts      TIMESTAMP,                     -- [+]
    settled_ts        TIMESTAMP,
    value_date_id     INTEGER REFERENCES dim_date(date_id),  -- [+]
    status            TEXT NOT NULL CHECK (status IN ('pending','completed','rejected','returned','delayed')),  -- see lifecycle/timing.py and asof.py
    failure_reason    TEXT REFERENCES dim_failure_reason(reason_code),
    is_stp            BOOLEAN NOT NULL,              -- [+] no manual touch end-to-end (analysis 3)
    repair_count      INTEGER NOT NULL DEFAULT 0,    -- [+] manual repairs (analysis 3)
    missed_cutoff     BOOLEAN NOT NULL DEFAULT FALSE -- [+] rolled to next business day (analysis 3)
);

-- [+] Append-only lifecycle log: one row per status transition / gpi hop.
CREATE TABLE IF NOT EXISTS fact_payment_event (
    event_id     INTEGER PRIMARY KEY,
    payment_id   TEXT NOT NULL REFERENCES fact_payment(payment_id),
    event_ts     TIMESTAMP NOT NULL,
    status       TEXT NOT NULL,     -- CREATED, APPROVED, SUBMITTED, VALIDATED, SCREENED, ROUTED, IN_FLIGHT, REPAIRED, HELD, SETTLED, CREDITED, REJECTED, RETURNED
    bank_id      TEXT REFERENCES dim_bank(bank_id),
    hop_seq      INTEGER,
    reason_code  TEXT REFERENCES dim_failure_reason(reason_code)
);

CREATE TABLE IF NOT EXISTS fact_balance (
    date_id          INTEGER NOT NULL REFERENCES dim_date(date_id),
    account_id       TEXT    NOT NULL REFERENCES dim_account(account_id),
    closing_balance  NUMERIC NOT NULL,
    currency_code    TEXT    NOT NULL REFERENCES dim_currency(currency_code),
    closing_balance_sgd NUMERIC NOT NULL,        -- [+]
    PRIMARY KEY (date_id, account_id)
);

CREATE TABLE IF NOT EXISTS fact_sweep (              -- [+] pooling transfers (analysis 6)
    sweep_id          TEXT PRIMARY KEY,
    date_id           INTEGER NOT NULL REFERENCES dim_date(date_id),
    from_account_id   TEXT NOT NULL REFERENCES dim_account(account_id),
    to_account_id     TEXT NOT NULL REFERENCES dim_account(account_id),
    amount            NUMERIC NOT NULL,
    currency_code     TEXT NOT NULL REFERENCES dim_currency(currency_code),
    sweep_ts          TIMESTAMP NOT NULL,
    sweep_type        TEXT NOT NULL CHECK (sweep_type IN ('zba','internal'))  -- nightly zero-balancing / same-entity funding
);

-- [+] The bank's view (camt.053-like), deliberately separate from fact_payment (analysis 8).
CREATE TABLE IF NOT EXISTS fact_statement_line (
    line_id           TEXT PRIMARY KEY,
    account_id        TEXT NOT NULL REFERENCES dim_account(account_id),
    booking_date_id   INTEGER NOT NULL REFERENCES dim_date(date_id),
    value_date_id     INTEGER NOT NULL REFERENCES dim_date(date_id),
    credit_debit      TEXT NOT NULL CHECK (credit_debit IN ('CRDT','DBIT')),
    amount            NUMERIC NOT NULL,
    currency_code     TEXT NOT NULL REFERENCES dim_currency(currency_code),
    bank_tx_code      TEXT NOT NULL,  -- RCDT received / ICDT issued / CHRG charges / RTRN return / CARD / INTC / SWEEP
    bank_reference    TEXT NOT NULL,
    remittance_info   TEXT,           -- clean / truncated / typo'd / missing
    counterparty_name TEXT
);

CREATE TABLE IF NOT EXISTS fact_fx_hedge (           -- [+] optional (analysis 7 hedged share)
    hedge_id        TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES dim_entity(entity_id),
    trade_date_id   INTEGER NOT NULL REFERENCES dim_date(date_id),
    maturity_date_id INTEGER NOT NULL REFERENCES dim_date(date_id),
    buy_currency    TEXT NOT NULL REFERENCES dim_currency(currency_code),
    sell_currency   TEXT NOT NULL REFERENCES dim_currency(currency_code),
    buy_amount      NUMERIC NOT NULL,
    sell_amount     NUMERIC NOT NULL,
    forward_rate    NUMERIC NOT NULL    -- functional-currency units per 1 unit of the foreign currency
);

CREATE INDEX IF NOT EXISTS ix_payment_initiated ON fact_payment(initiated_ts);
CREATE INDEX IF NOT EXISTS ix_payment_corridor  ON fact_payment(sender_country, receiver_country);
CREATE INDEX IF NOT EXISTS ix_payment_status    ON fact_payment(status);
CREATE INDEX IF NOT EXISTS ix_event_payment     ON fact_payment_event(payment_id, event_ts);
