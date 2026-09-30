"""Discrete-event core (v1-v2).

A heap of (sim_ts_utc, seq, event) tuples. Each handler may schedule new events.
Handlers are registered by the engines below:

  business.events   -> InvoiceIssued, InvoiceDue, PaymentRun, Payroll, TaxDue, CardSpend
  lifecycle         -> PaymentTransition (CREATED ... SETTLED/REJECTED/RETURNED)
  ledger            -> ValueDatePosting, EndOfDaySnapshot (per entity timezone), NightlySweep
  recon             -> StatementGeneration (per account, bank EOD)
  inject            -> post-processing on emitted rows (anomalies before ledger, DQ after)
"""
