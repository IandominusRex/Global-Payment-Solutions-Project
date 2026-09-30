"""Human-friendly exports of the warehouse: one Excel workbook, CSVs, and a GitHub-readable dataset card.

  export_workbook  every table as a sheet of a single .xlsx (plus an "About" sheet)
  export_csv       every table as its own CSV
  export_docs      docs/dataset/dataset-card.md + a small sample CSV per table (GitHub renders CSVs as tables)
  refresh_exports  the above, driven by the `output` config; called after a backfill and while streaming
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import xlsxwriter

from treasury.simulator.config import SimulationConfig
from treasury.simulator.sinks.warehouse import get_engine

SCHEMA_DIR = Path(__file__).resolve().parents[4] / "sql" / "schema"
SAMPLE_ROWS = 50

TABLE_ABOUT = {
    "dim_date": "Calendar of days (yyyymmdd key) with week, month, quarter and weekend flags.",
    "dim_calendar": "Which days are business days in each country (holidays differ by country).",
    "dim_country": "Countries, their region, time zone and an illustrative risk rating.",
    "dim_currency": "The 6 currencies in the data, and whether each is restricted (CNY, INR).",
    "dim_bank": "The fictional banks that hold the group's accounts.",
    "dim_entity": "The 11 legal entities of the fictional group (e.g. Shanghai Mfg, Germany GmbH).",
    "dim_account": "Bank accounts owned by the entities, with overdraft limits, target balances, interest rates.",
    "dim_counterparty": "Customers, suppliers, payroll groups, tax authorities and sister companies.",
    "dim_payment_type": "The 13 payment rails (FAST, SEPA, SWIFT, card...) with cut-offs and normal failure rates.",
    "dim_failure_reason": "Why a payment can fail (ISO 20022 style reason codes).",
    "dim_purpose_code": "Why the money moved (salary, supplier, tax, intercompany...).",
    "fact_payment": "One row per payment: who paid whom, how much, on which rail, and how it ended.",
    "fact_payment_event": "The step-by-step history of each payment (created, submitted, settled, rejected...).",
    "fact_invoice": "Invoices the group issued (AR) or received (AP), and whether they were paid.",
    "fact_statement_line": "The bank's own record of each movement, as on a bank statement (used for reconciliation).",
    "fact_balance": "Closing balance of every account on every day.",
    "fact_sweep": "Nightly transfers that move surplus cash between accounts (cash pooling).",
    "fact_fx_hedge": "Monthly currency forward contracts that protect against exchange-rate moves.",
    "fact_fx_rate": "Daily exchange rates of each currency into Singapore dollars.",
}

# Plain-English fallbacks for columns the SQL schema does not comment. (table, column) wins over column.
COLUMN_HINTS = {
    "account_id": "Bank account (joins to dim_account)",
    "entity_id": "Group company (joins to dim_entity)",
    "bank_id": "Bank (joins to dim_bank)",
    "counterparty_id": "The other party (joins to dim_counterparty)",
    "currency_code": "3-letter currency code, e.g. SGD, USD",
    "type_id": "Payment rail (joins to dim_payment_type)",
    "date_id": "Date as yyyymmdd",
    "country_code": "2-letter country code",
    "payment_id": "Unique payment id",
    "direction": "OUT = we paid, IN = we received",
    "amount": "Amount in the row's own currency",
    "initiated_ts": "When the payment was created (UTC)",
    "submitted_ts": "When it was sent to the bank (UTC)",
    "settled_ts": "When the money arrived (UTC); empty if not settled",
    "value_date_id": "Date the money counts (yyyymmdd)",
    "receiver_country": "Country of the receiving party",
    "charge_bearer": "Who pays fees: OUR, SHA (shared) or BEN",
    "fees": "Bank fees charged",
    "purpose_code": "Why the money moved (joins to dim_purpose_code)",
    "is_intercompany": "True if between two group companies",
    "failure_reason": "Reason code if it failed",
    "is_fixable_at_source": "True if the sender could have avoided this failure",
    "description": "Plain-English description",
    "reason_code": "Failure reason code",
    "name": "Name (fictional)",
    "country": "2-letter country code",
    "timezone": "Time zone, e.g. Asia/Singapore",
    "risk_rating": "Illustrative risk level: low, medium or high",
    "region": "APAC, EMEA or AMER",
    "settlement_currency": "Currency used when paying into this country",
    "decimals": "Decimal places the currency uses",
    "legal_type": "Company form, e.g. GmbH, WFOE, Inc",
    "functional_currency": "The entity's home currency",
    "is_in_house_bank": "True for the group treasury acting as internal bank",
    "bic": "Fictional bank code (SWIFT style)",
    "is_correspondent": "True if used as an intermediary for cross-border payments",
    "account_type": "operating, collection, disbursement or payroll",
    "overdraft_limit": "How far below zero the account may go",
    "target_balance": "Cash level the treasury aims to keep",
    "is_pooled": "True if swept to a header account nightly",
    "pool_header_account_id": "Account the surplus is swept to",
    "is_business_day": "False on weekends and holidays",
    "holiday_name": "Public holiday name, if any",
    "avg_days_late": "How late this party usually pays (days)",
    "data_quality_score": "How clean their payment references are (0-1)",
    "uses_virtual_account": "True if they pay into a virtual account (clean references)",
    "week": "ISO week number",
    "month": "Month number",
    "quarter": "Quarter 1-4",
    "year": "Year",
    "date": "Calendar date",
    "is_weekend": "True on Saturday/Sunday",
    "is_month_end": "True on the last day of the month",
    "is_quarter_end": "True on the last day of the quarter",
    "rail": "Payment scheme or network, e.g. SEPA_CT, FEDWIRE",
    "is_instant": "True if it settles in seconds",
    "is_batch": "True if sent in batched files",
    "is_cross_border": "True if it crosses a border",
    "cut_off_local": "Latest local time to submit for same-day processing",
    "typical_settlement_days": "Normal days to settle",
    "base_fail_rate": "Normal share of payments that fail",
    "closing_balance": "End-of-day balance in the account's currency",
    "closing_balance_sgd": "Same, converted to SGD",
    "hedge_id": "Unique hedge id",
    "trade_date_id": "Date the forward was booked",
    "maturity_date_id": "Date it settles",
    "buy_currency": "Currency bought",
    "sell_currency": "Currency sold",
    "buy_amount": "Amount bought",
    "sell_amount": "Amount sold",
    "rate_to_sgd": "1 unit of the currency in SGD",
    "invoice_id": "Unique invoice id",
    "invoice_ref": "Invoice number as printed",
    "issue_date_id": "Date issued",
    "due_date_id": "Date payment is due",
    "event_id": "Unique event id",
    "event_ts": "When the step happened (UTC)",
    "hop_seq": "Order of the bank hop for cross-border payments",
    "line_id": "Unique statement line id",
    "booking_date_id": "Date the bank booked it",
    "credit_debit": "CRDT = money in, DBIT = money out",
    "bank_reference": "The bank's reference text (can be truncated)",
    "counterparty_name": "Name as shown on the statement",
    "sweep_id": "Unique sweep id",
    "from_account_id": "Account cash moved out of",
    "to_account_id": "Account cash moved into",
    "status": "Where the payment or event stands, e.g. CREATED, SUBMITTED, SETTLED, REJECTED",
    "sweep_ts": "When the transfer ran (UTC)",
}


def _tables(conn) -> list[str]:
    rows = conn.exec_driver_sql("select name from sqlite_master where type = 'table' order by name")
    return [r[0] for r in rows]


def _read(conn, name: str) -> pd.DataFrame:
    return pd.read_sql_query(f"select * from {name}", conn)


def export_workbook(cfg: SimulationConfig, path: Path, log=print) -> Path:
    t0 = time.perf_counter()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.xlsx")
    wb = xlsxwriter.Workbook(tmp, {"constant_memory": True, "nan_inf_to_errors": True})
    head = wb.add_format({"bold": True, "bg_color": "#1F3A5F", "font_color": "#FFFFFF"})
    about = wb.add_worksheet("About")
    sheets = []
    with get_engine(cfg.output.warehouse_url).connect() as conn:
        for name in _tables(conn):
            df = _read(conn, name)
            ws = wb.add_worksheet(name)
            ws.write_row(0, 0, list(df.columns), head)
            for i, row in enumerate(df.itertuples(index=False, name=None), start=1):
                ws.write_row(i, 0, [None if pd.isna(v) else v for v in row])
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(len(df), 1), max(len(df.columns) - 1, 0))
            ws.set_column(0, len(df.columns) - 1, 16)
            sheets.append((name, len(df)))
    title = wb.add_format({"bold": True, "font_size": 14})
    about.write(0, 0, "Treasury Payments Analytics - synthetic dataset", title)
    about.write(1, 0, f"Last refreshed (UTC): {datetime.now(UTC):%Y-%m-%d %H:%M}")
    about.write(2, 0, "All data is synthetic. Rebuilt automatically when the simulator runs.")
    about.write_row(4, 0, ["Sheet", "Rows", "What it holds"], head)
    for i, (name, n) in enumerate(sheets, start=5):
        about.write_url(i, 0, f"internal:'{name}'!A1", string=name)
        about.write(i, 1, n)
        about.write(i, 2, TABLE_ABOUT.get(name, ""))
    about.set_column(0, 0, 24)
    about.set_column(1, 1, 10)
    about.set_column(2, 2, 100)
    wb.close()
    tmp.replace(path)
    log(f"Workbook: {len(sheets)} sheets -> {path} ({time.perf_counter() - t0:.0f}s)")
    return path


def export_csv(cfg: SimulationConfig, out: Path, log=print) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with get_engine(cfg.output.warehouse_url).connect() as conn:
        for name in _tables(conn):
            df = _read(conn, name)
            df.to_csv(out / f"{name}.csv", index=False)
            log(f"  {name:<20} {len(df):>8,} rows -> {out / (name + '.csv')}")


def _column_docs() -> dict[str, dict[str, str]]:
    """Column descriptions, taken from the `-- comments` in sql/schema/*.sql."""
    docs: dict[str, dict[str, str]] = {}
    table = None
    for f in sorted(SCHEMA_DIR.glob("*.sql")):
        for line in f.read_text().splitlines():
            m = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", line)
            if m:
                table = m.group(1)
                docs[table] = {}
                continue
            if table is None:
                continue
            if line.startswith(");"):
                table = None
                continue
            m = re.match(r"\s+(\w+)\s+[A-Z]+\b.*?(?:--\s*(.*))?$", line)
            if m and m.group(1).upper() not in {"PRIMARY", "UNIQUE", "FOREIGN", "CHECK"}:
                note = re.sub(r"^\[\+\]\s*", "", (m.group(2) or "").strip())
                docs[table][m.group(1)] = note
    return docs


def _md_table(headers: list[str], rows: list[list]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
    return "\n".join(out)


def _glance(conn) -> str:
    q = lambda sql: conn.exec_driver_sql(sql).fetchall()  # noqa: E731
    lo, hi, n, ext = q("select substr(min(initiated_ts),1,10), substr(max(initiated_ts),1,10), count(*), "
                       "sum(is_intercompany = 0) from fact_payment")[0]
    ok = q("select round(100.0 * avg(status = 'completed'), 1) from fact_payment where is_intercompany = 0")[0][0]
    vol = q("select round(sum(amount_sgd) / 1e9, 2) from fact_payment "
            "where is_intercompany = 0 and status = 'completed'")[0][0]
    facts = [["Period covered", f"{lo} to {hi}"],
             ["Payments", f"{n:,} ({ext:,} external; the rest are the two legs of intercompany transfers)"],
             ["Value of completed external payments", f"S${vol} billion"],
             ["Completed successfully", f"{ok}% of external payments"],
             ["Entities / accounts / counterparties", "{} / {} / {}".format(
                 *[q(f"select count(*) from {t}")[0][0] for t in ("dim_entity", "dim_account", "dim_counterparty")])],
             ["Currencies", ", ".join(r[0] for r in q("select currency_code from dim_currency order by 1"))]]
    rails = q("select t.rail, count(*), round(100.0 * avg(p.status in ('rejected','returned')), 1) from fact_payment p "
              "join dim_payment_type t using(type_id) where p.is_intercompany = 0 group by 1 order by 2 desc")
    corr = q("select sender_country || ' -> ' || receiver_country, count(*), round(sum(amount_sgd) / 1e6, 1) "
             "from fact_payment where is_intercompany = 0 and sender_country <> receiver_country "
             "group by 1 order by 3 desc limit 5")
    return "\n\n".join([
        _md_table(["", ""], facts),
        "**Payments by rail** (share that failed shown alongside)\n\n" + _md_table(
            ["Rail", "Payments", "Failed / returned %"],
            [[r, f"{c:,}", f"{f}%"] for r, c, f in rails]),
        "**Top cross-border corridors by value**\n\n" + _md_table(
            ["Corridor (sender -> receiver)", "Payments", "Value (S$ million)"],
            [[a, f"{c:,}", v] for a, c, v in corr]),
    ])


def export_docs(cfg: SimulationConfig, out: Path, log=print) -> Path:
    """Dataset card + per-table sample CSVs. Committed to git so GitHub can preview the data."""
    samples = out / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    docs = _column_docs()
    parts = []
    with get_engine(cfg.output.warehouse_url).connect() as conn:
        glance = _glance(conn)
        names = _tables(conn)
        for name in names:
            df = _read(conn, name)
            step = max(len(df) // SAMPLE_ROWS, 1)
            df.iloc[::step].head(SAMPLE_ROWS).to_csv(samples / f"{name}.csv", index=False)
            types = {r[1]: r[2] for r in conn.exec_driver_sql(f"pragma table_info({name})")}
            cols = [[c, types.get(c, "").lower(), docs.get(name, {}).get(c) or COLUMN_HINTS.get(c, "")]
                    for c in df.columns]
            example = df.iloc[len(df) // 2]
            ex = [[c, "" if pd.isna(v) else v] for c, v in example.items()]
            cols_md = _md_table(["Column", "Type", "Meaning"], cols)
            ex_md = _md_table(["Column", "Value"], ex)
            parts.append(
                f"### `{name}`\n\n{TABLE_ABOUT.get(name, '')} **{len(df):,} rows, {len(df.columns)} columns.** "
                f"[Open a {SAMPLE_ROWS}-row sample](samples/{name}.csv)\n\n"
                f"<details><summary>Columns</summary>\n\n{cols_md}\n\n</details>\n\n"
                f"<details><summary>One example row</summary>\n\n{ex_md}\n\n</details>")
        counts = [[f"[`{n}`](#{n})", conn.exec_driver_sql(f"select count(*) from {n}").scalar(),
                   TABLE_ABOUT.get(n, "")] for n in names]
    text = f"""# Dataset card: Treasury Payments (synthetic)

_Auto-generated by `treasury-sim export-all` from the small-profile warehouse. Do not edit by hand._
_Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC, seed {cfg.seed}._

## What this is

A fictional multinational group's payments, bank accounts, invoices and FX activity over 24 months,
generated by a simulator because no public dataset covers this. **All names, banks and BICs are made up.**
It is a star schema: `dim_*` tables describe things (entities, accounts, banks); `fact_*` tables record
what happened (payments, balances, invoices). Payments join to `dim_account` on `account_id`, to
`dim_counterparty` on `counterparty_id`, and to `dim_payment_type` on `type_id`.

Answer keys (which payments are planted anomalies or dirty data, which invoice each payment settled) are
kept out of the warehouse, in `data_small/truth/`, so analysis code cannot peek.

## At a glance

{glance}

## Tables

{_md_table(['Table', 'Rows', 'What it holds'], counts)}

## Table details

{chr(10).join(parts)}
"""
    (out / "dataset-card.md").write_text(text)
    log(f"Dataset card + {len(names)} samples -> {out}")
    return out / "dataset-card.md"


def refresh_exports(cfg: SimulationConfig, docs: bool = True, log=print) -> None:
    """Rebuild every export the config asks for. Cheap to call; skipped when nothing is configured."""
    if cfg.output.export_dir:
        d = Path(cfg.output.export_dir)
        export_workbook(cfg, d / "treasury_dataset.xlsx", log)
        export_csv(cfg, d / "csv", lambda *_: None)
    if docs and cfg.output.docs_dir:
        export_docs(cfg, Path(cfg.output.docs_dir), log)
