-- Star schema: dimensions. Extends the blueprint (lesson 8) - additions are marked [+].
-- Portable SQL: runs on SQLite and PostgreSQL.

CREATE TABLE IF NOT EXISTS dim_date (
    date_id         INTEGER PRIMARY KEY,      -- yyyymmdd
    date            DATE    NOT NULL,
    week            INTEGER NOT NULL,
    month           INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,
    year            INTEGER NOT NULL,
    day_of_week     INTEGER NOT NULL,         -- [+] 0 = Monday
    is_weekend      BOOLEAN NOT NULL,         -- [+]
    is_month_end    BOOLEAN NOT NULL,         -- [+]
    is_quarter_end  BOOLEAN NOT NULL          -- [+]
);

CREATE TABLE IF NOT EXISTS dim_country (             -- [+]
    country_code        TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    region              TEXT NOT NULL,
    timezone            TEXT NOT NULL,
    risk_rating         TEXT NOT NULL CHECK (risk_rating IN ('low','medium','high')),
    settlement_currency TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_currency (
    currency_code   TEXT PRIMARY KEY,
    name            TEXT    NOT NULL,
    decimals        INTEGER NOT NULL,         -- [+]
    is_restricted   BOOLEAN NOT NULL          -- [+] CNY / INR
);

CREATE TABLE IF NOT EXISTS dim_bank (                -- [+]
    bank_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    country           TEXT NOT NULL REFERENCES dim_country(country_code),
    bic               TEXT NOT NULL,
    is_correspondent  BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_entity (
    entity_id           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    country             TEXT NOT NULL REFERENCES dim_country(country_code),
    legal_type          TEXT NOT NULL,
    functional_currency TEXT NOT NULL REFERENCES dim_currency(currency_code),  -- [+]
    is_in_house_bank    BOOLEAN NOT NULL,                                      -- [+]
    timezone            TEXT NOT NULL                                          -- [+]
);

CREATE TABLE IF NOT EXISTS dim_account (
    account_id              TEXT PRIMARY KEY,
    entity_id               TEXT NOT NULL REFERENCES dim_entity(entity_id),
    bank_id                 TEXT NOT NULL REFERENCES dim_bank(bank_id),
    currency_code           TEXT NOT NULL REFERENCES dim_currency(currency_code),
    account_type            TEXT NOT NULL,
    overdraft_limit         NUMERIC NOT NULL,     -- [+]
    target_balance          NUMERIC NOT NULL,     -- [+]
    is_pooled               BOOLEAN NOT NULL,     -- [+]
    pool_header_account_id  TEXT REFERENCES dim_account(account_id),  -- [+]
    credit_rate             NUMERIC NOT NULL,     -- [+] annual, values idle cash (analysis 6)
    debit_rate              NUMERIC NOT NULL      -- [+] annual, values overdraft cost (analysis 6)
);

CREATE TABLE IF NOT EXISTS dim_counterparty (
    counterparty_id       TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    counterparty_type     TEXT NOT NULL,          -- [+] customer/supplier/employee_group/tax_authority/intercompany
    country               TEXT NOT NULL REFERENCES dim_country(country_code),
    risk_rating           TEXT NOT NULL,
    avg_days_late         NUMERIC NOT NULL,       -- [+]
    data_quality_score    NUMERIC NOT NULL,       -- [+]
    uses_virtual_account  BOOLEAN NOT NULL,       -- [+]
    home_entity_id        TEXT REFERENCES dim_entity(entity_id),  -- [+] entity it mainly trades with
    first_seen_date       DATE                    -- [+] new-beneficiary rule (analysis 9)
);

CREATE TABLE IF NOT EXISTS dim_payment_type (
    type_id                  INTEGER PRIMARY KEY,
    rail                     TEXT    NOT NULL UNIQUE,
    is_instant               BOOLEAN NOT NULL,
    is_batch                 BOOLEAN NOT NULL,
    is_cross_border          BOOLEAN NOT NULL,    -- [+]
    cut_off_local            TEXT,                -- [+]
    typical_settlement_days  INTEGER,             -- [+]
    base_fail_rate           NUMERIC NOT NULL     -- [+]
);

CREATE TABLE IF NOT EXISTS dim_failure_reason (      -- [+]
    reason_code           TEXT PRIMARY KEY,
    description           TEXT NOT NULL,
    category              TEXT NOT NULL,     -- data / funds / compliance / technical / other
    is_fixable_at_source  BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_purpose_code (        -- [+]
    purpose_code  TEXT PRIMARY KEY,
    description   TEXT NOT NULL
);

-- [+] Business days differ per country; the blueprint's single is_business_day cannot express that.
CREATE TABLE IF NOT EXISTS dim_calendar (
    date_id         INTEGER NOT NULL REFERENCES dim_date(date_id),
    country_code    TEXT    NOT NULL REFERENCES dim_country(country_code),
    is_business_day BOOLEAN NOT NULL,
    holiday_name    TEXT,
    PRIMARY KEY (date_id, country_code)
);
