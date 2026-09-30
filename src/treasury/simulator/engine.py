"""Discrete-event core (v2).

v1 does not use this: business/events.py and lifecycle/simple.py run as one vectorised
pass per flow, which is enough when outcomes don't depend on balances. v2 needs this
event loop because failures (AM04) and funding depend on the running ledger.

A heap of (sim_ts_utc, seq, event) tuples. Each handler may schedule new events.
Handlers are registered by the engines below:

  business.events   -> InvoiceIssued, InvoiceDue, PaymentRun, Payroll, TaxDue, CardSpend
  lifecycle         -> PaymentTransition (CREATED ... SETTLED/REJECTED/RETURNED)
  ledger            -> ValueDatePosting, EndOfDaySnapshot (per entity timezone), NightlySweep
  recon             -> StatementGeneration (per account, bank EOD)
  inject            -> post-processing on emitted rows (anomalies before ledger, DQ after)
"""
