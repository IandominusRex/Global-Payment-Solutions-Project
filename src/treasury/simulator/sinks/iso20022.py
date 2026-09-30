"""ISO 20022 camt.053 (bank-to-customer statement) export from the warehouse.

  treasury-sim export-camt053 --account A001 --date 2026-09-15

Builds one statement for one account and booking date: opening/closing booked
balances (OPBD/CLBD from fact_balance) and one Ntry per fact_statement_line. This
is the format a treasury system ingests every morning, which is why the project's
reconciliation starts from statement lines rather than from the ERP's own records.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, text

NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"


def _sub(parent: ET.Element, tag: str, text_: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, f"{{{NS}}}{tag}", attrs)
    if text_ is not None:
        el.text = text_
    return el


def _amount(parent: ET.Element, value: float, ccy: str) -> None:
    _sub(parent, "Amt", f"{abs(value):.2f}", Ccy=ccy)
    _sub(parent, "CdtDbtInd", "CRDT" if value >= 0 else "DBIT")


def _balance(stmt: ET.Element, code: str, value: float, ccy: str, day: date) -> None:
    bal = _sub(stmt, "Bal")
    _sub(_sub(_sub(bal, "Tp"), "CdOrPrtry"), "Cd", code)
    _amount(bal, value, ccy)
    _sub(_sub(bal, "Dt"), "Dt", day.isoformat())


def build_camt053(engine: Engine, account_id: str, day: date) -> ET.ElementTree:
    did, prev = int(day.strftime("%Y%m%d")), int((day - timedelta(days=1)).strftime("%Y%m%d"))
    with engine.connect() as conn:
        acct = conn.execute(text("SELECT a.account_id, a.currency_code, b.bic FROM dim_account a "
                                 "JOIN dim_bank b USING (bank_id) WHERE a.account_id = :a"),
                            {"a": account_id}).mappings().first()
        if acct is None:
            raise ValueError(f"unknown account {account_id}")
        bal = dict(conn.execute(text("SELECT date_id, closing_balance FROM fact_balance WHERE account_id = :a "
                                     "AND date_id IN (:p, :d)"), {"a": account_id, "p": prev, "d": did}).all())
        lines = pd.read_sql(text("SELECT * FROM fact_statement_line WHERE account_id = :a AND booking_date_id = :d "
                                 "ORDER BY line_id"), conn, params={"a": account_id, "d": did})
    if did not in bal:
        raise ValueError(f"no closing balance for {account_id} on {day}")
    ccy = acct["currency_code"]

    ET.register_namespace("", NS)
    doc = ET.Element(f"{{{NS}}}Document")
    root = _sub(doc, "BkToCstmrStmt")
    hdr = _sub(root, "GrpHdr")
    _sub(hdr, "MsgId", f"STMT-{account_id}-{did}")
    _sub(hdr, "CreDtTm", datetime.combine(day + timedelta(days=1), datetime.min.time()).isoformat())
    stmt = _sub(root, "Stmt")
    _sub(stmt, "Id", f"{account_id}-{did}")
    _sub(stmt, "CreDtTm", datetime.combine(day + timedelta(days=1), datetime.min.time()).isoformat())
    acc = _sub(stmt, "Acct")
    _sub(_sub(_sub(acc, "Id"), "Othr"), "Id", account_id)
    _sub(acc, "Ccy", ccy)
    _sub(_sub(_sub(acc, "Svcr"), "FinInstnId"), "BICFI", acct["bic"])
    _balance(stmt, "OPBD", float(bal.get(prev, 0.0)), ccy, day - timedelta(days=1))
    _balance(stmt, "CLBD", float(bal[did]), ccy, day)

    for ln in lines.itertuples():
        ntry = _sub(stmt, "Ntry")
        _amount(ntry, ln.amount if ln.credit_debit == "CRDT" else -ln.amount, ccy)
        _sub(_sub(ntry, "Sts"), "Cd", "BOOK")
        _sub(_sub(ntry, "BookgDt"), "Dt", pd.to_datetime(str(ln.booking_date_id)).date().isoformat())
        _sub(_sub(ntry, "ValDt"), "Dt", pd.to_datetime(str(ln.value_date_id)).date().isoformat())
        _sub(ntry, "AcctSvcrRef", ln.bank_reference)
        _sub(_sub(_sub(ntry, "BkTxCd"), "Prtry"), "Cd", ln.bank_tx_code)
        tx = _sub(_sub(ntry, "NtryDtls"), "TxDtls")
        if isinstance(ln.counterparty_name, str):
            party = "Dbtr" if ln.credit_debit == "CRDT" else "Cdtr"
            _sub(_sub(_sub(tx, "RltdPties"), party), "Nm", ln.counterparty_name)
        if isinstance(ln.remittance_info, str):
            _sub(_sub(tx, "RmtInf"), "Ustrd", ln.remittance_info)
    ET.indent(doc)
    return ET.ElementTree(doc)


def export_camt053(engine: Engine, account_id: str, day: date, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    build_camt053(engine, account_id, day).write(out, encoding="utf-8", xml_declaration=True)
    return out
