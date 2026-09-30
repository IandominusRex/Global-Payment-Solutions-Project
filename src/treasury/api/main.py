"""FastAPI app. Run: uvicorn treasury.api.main:app --reload  (needs the [api] extra)."""

from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Treasury Payments API (synthetic)", version="0.1.0")


def require_token(authorization: str = Header(default="")) -> None:
    expected = os.environ.get("TREASURY_API_TOKEN")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Missing or invalid bearer token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Planned (blueprint Part 4), each behind Depends(require_token):
#   GET /balances?account_id=&entity_id=&currency=
#   GET /payments/{payment_id}/status        (current state + event trail)
#   GET /kpis/payments                       (STP rate, failure rate, P50/P90 time)
#   GET /forecast/cash?days=91               (13 weeks)
#   GET /exceptions                          (flagged payments)
