"""Business Event Engine (v1): what causes money to move.

Invoices first, payments second. Flow types, schedules and amount shapes are in
docs/architecture.md. Seasonality: month/quarter-end, Monday peaks, CNY dip for CN,
country holidays from dim_calendar.

Intercompany funding is triggered by a *rule* on projected balances
(today's balance + scheduled AP/payroll/tax over the next N business days), not by
the forecast model built in analysis 5 - that would make the data depend on the
thing being evaluated.
"""
