"""Payment Lifecycle Engine (v2).

CREATED -> APPROVED -> SUBMITTED -> VALIDATED -> SCREENED -> ROUTED
        -> IN_FLIGHT (1..n hops, cross-border) -> SETTLED -> CREDITED
Side exits: REPAIRED (manual fix, breaks STP), HELD (sanctions review),
REJECTED (pain.002 RJCT), RETURNED (pacs.004, days after SETTLED).

Every transition emits a fact_payment_event row and updates fact_payment.
Failure probability = f(rail base rate, corridor, counterparty data_quality_score,
channel, amount). Reason codes are drawn so that ~3 data-quality reasons cause
~80% of failures (analysis 4 Pareto).
"""
