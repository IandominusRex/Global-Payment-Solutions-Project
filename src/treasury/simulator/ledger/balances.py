"""Ledger / Balance Engine (v2).

- Money moves only on SETTLED, on the value date.
- Outgoing payment breaching overdraft_limit -> AM04 rejection or IC funding (config).
- Closing balances snapshotted at each entity's local EOD -> fact_balance.
- Nightly sweeps zero-balance pooled accounts into the pool header -> fact_sweep.
- Invariant (tested): opening + sum(settled flows) + sweeps == closing, per account/day.
"""
