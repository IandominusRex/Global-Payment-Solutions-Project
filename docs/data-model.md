# Data model

How the warehouse tables (`data_small/warehouse/treasury.sqlite`) fit together. The source of
truth is the DDL in [`sql/schema/`](../sql/schema/); this page is the picture of it.

It is a **star schema**:

- `dim_*` tables describe *things*: companies, accounts, banks, counterparties. They change rarely.
- `fact_*` tables record *what happened*: payments, balances, invoices. They grow every day.

Every `*_date_id` column (yyyymmdd) joins to `dim_date`, and every `currency_code` joins to
`dim_currency`. Those two links are left out of the diagram, because drawing them from every
table makes it unreadable.

## Full diagram

```mermaid
erDiagram
    dim_country ||--o{ dim_entity : "is home of"
    dim_country ||--o{ dim_bank : "is home of"
    dim_country ||--o{ dim_counterparty : "is home of"
    dim_country ||--o{ dim_calendar : "has holidays in"
    dim_date ||--o{ dim_calendar : "one row per country"

    dim_entity ||--o{ dim_account : owns
    dim_bank ||--o{ dim_account : holds
    dim_account |o--o{ dim_account : "pool header of"
    dim_entity |o--o{ dim_counterparty : "main trading partner of"

    dim_account ||--o{ fact_payment : "our side of"
    dim_counterparty ||--o{ fact_payment : "other side of"
    dim_payment_type ||--o{ fact_payment : "rail used by"
    dim_failure_reason |o--o{ fact_payment : "explains failure of"
    dim_purpose_code |o--o{ fact_payment : "purpose of"
    fact_payment ||--|{ fact_payment_event : "history of"
    dim_bank |o--o{ fact_payment_event : "processed hop"

    dim_entity ||--o{ fact_invoice : issues
    dim_counterparty ||--o{ fact_invoice : "billed or billing"

    dim_account ||--o{ fact_balance : "closing balance per day"
    dim_account ||--o{ fact_sweep : "from / to"
    dim_account ||--o{ fact_statement_line : "bank's view of"
    dim_entity ||--o{ fact_fx_hedge : trades
    dim_currency ||--o{ fact_fx_rate : "daily rate of"

    dim_entity {
        text entity_id PK
        text country FK
        text functional_currency FK
        bool is_in_house_bank
        text primary_channel
    }
    dim_account {
        text account_id PK
        text entity_id FK
        text bank_id FK
        text currency_code FK
        bool is_pooled
        text pool_header_account_id FK
        num overdraft_limit
        num target_balance
    }
    dim_counterparty {
        text counterparty_id PK
        text counterparty_type
        text country FK
        bool uses_virtual_account
        date first_seen_date
    }
    dim_payment_type {
        int type_id PK
        text rail
        bool is_cross_border
        text cut_off_local
    }
    fact_payment {
        text payment_id PK
        text end_to_end_id "shared by both intercompany legs"
        text direction "OUT or IN"
        text account_id FK
        text counterparty_id FK
        int type_id FK
        text sender_country
        text receiver_country
        num amount
        num amount_sgd
        text status
        text failure_reason FK
        bool is_intercompany
        bool is_stp
        ts initiated_ts
        ts settled_ts
    }
    fact_payment_event {
        int event_id PK
        text payment_id FK
        ts event_ts
        text status
        text bank_id FK
        int hop_seq
    }
    fact_invoice {
        text invoice_id PK
        text direction "AR or AP"
        text entity_id FK
        text counterparty_id FK
        text invoice_ref
        num amount
        int due_date_id
        text status
    }
    fact_balance {
        int date_id PK
        text account_id PK
        num closing_balance
        num closing_balance_sgd
    }
    fact_statement_line {
        text line_id PK
        text account_id FK
        text credit_debit
        num amount
        text remittance_info "messy on purpose"
    }
    fact_sweep {
        text sweep_id PK
        text from_account_id FK
        text to_account_id FK
        num amount
    }
    fact_fx_rate {
        int date_id PK
        text currency_code PK
        num rate_to_sgd
    }
    fact_fx_hedge {
        text hedge_id PK
        text entity_id FK
        text buy_currency
        text sell_currency
    }
```

## How to read it

**Follow a payment outward.** A row in `fact_payment` is one payment's *current state*.
From there:

1. `account_id` → `dim_account` → `dim_entity`: *which of our companies* paid or received.
2. `counterparty_id` → `dim_counterparty`: *who* was on the other side.
3. `type_id` → `dim_payment_type`: *which rail* it used (SWIFT, GIRO, SEPA…).
4. `payment_id` → `fact_payment_event`: *everything that happened to it*, one row per step
   (CREATED → APPROVED → SUBMITTED → … → SETTLED).

**Payments are one row per side we own.** `direction = 'OUT'` means money left one of our
accounts; `'IN'` means it arrived in one. An *intercompany* transfer (group company to group
company) touches two of our accounts, so it appears **twice**: an OUT leg and an IN leg,
sharing one `end_to_end_id`. Filter `is_intercompany = 0` for group totals, or every internal
transfer is counted twice.

**Balances are a daily snapshot.** `fact_balance` has one row per account per day, which is
what the forecast (analysis 5) and cash concentration (analysis 6) use.

## The links that are missing on purpose

Two joins you might expect **do not exist**, because finding them is the analysis:

| Missing link | Why | Where the answer key is |
|---|---|---|
| payment → invoice | In real life a receipt arrives with a reference like `INV-2291` (or a typo, or nothing), and treasury has to work out which invoice it pays. That's **reconciliation** (analysis 8). | `data_small/truth/payment_invoice.parquet` |
| statement line → payment | The bank's statement is a separate record of the same money. Matching it back to our payments is also analysis 8. | `data_small/truth/statement_payment.parquet` |

The `truth/` files are the answer key: never join them into analysis queries, only use them to
*score* your results afterwards.

## Try it

Open the database in [DBeaver](https://dbeaver.io/) (New Connection → SQLite → point it at
`data_small/warehouse/treasury.sqlite`) and run
[`sql/exploration/00_first_look.sql`](../sql/exploration/00_first_look.sql). Each query
there walks one part of this diagram.
