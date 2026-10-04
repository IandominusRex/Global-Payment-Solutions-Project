# 2026-10-03 · The first four SQL analyses

## Problem
With the cleaning pipeline skipped (see entry 04), the next job was the SQL analyses on the clean database.
I needed a way to run queries and keep the results in one place, and I wanted each analysis to be simple
enough to explain in a few lines.

## What I did
1. **Connected DBeaver to `data_small/clean/treasury.sqlite`** and ran the queries there. The finished
   queries and results are written up in the README, one section per analysis.
2. **Analysis 1, money movement.** Value and payment counts by currency, by corridor, by entity and by
   month. USD is the largest currency and SG -> CN is the biggest corridor (S$125m).
3. **Analysis 2, cross-border.** Average hours to settle and failure rate per corridor and per rail.
   SG -> VN is the slowest corridor and US -> IN fails the most among the large ones.
4. **Analysis 3, payment efficiency.** Straight-through processing (STP), repair and cut-off rates by
   channel, average hours by rail, and where the time goes inside a payment. LEGACY_FILE is clearly the
   worst channel (88% STP against 97.5% for API).
5. **Analysis 4, payment failures.** Failure reasons, repeat-offender counterparties, risk rating, rail
   and month. 81% of failures are "data" problems, and SWIFT fails the most (3.6%).
6. **Reformatted the README.** Queries are in their own code boxes and results are real tables.

## Decisions
- **No percentiles.** The p50 and p90 version needs two window functions and a ranking trick. For an
  exploratory project, the average plus "share of payments over 48 hours" says nearly the same thing and
  is much easier to read.
- **Compare like with like.** Two results looked wrong until I split them properly. Missing the cut-off
  seemed to make payments faster, and a sanctions hold seemed to add no time. Both were because the
  groups used different rails (slow CARD payments dragged the averages). Comparing within one rail
  showed the real effect: a missed cut-off costs about 3 times the hours on ACH, and a hold roughly
  doubles the time on SWIFT.
- **A failure rate needs the payments that did not fail.** My first failure-reason query joined to the
  failure-reason table, which only keeps failed payments, so the failure rate was 100% on every row. The
  top failure reasons need a share of all failures, not a failure rate.
- **Rank repeat offenders by failure rate, not by count.** Counting failures just lists the busiest
  suppliers. The rate, with a minimum number of failures, finds the ones with bad details.
- **Small groups are not findings.** FAST has a 3.5% failure rate, but only 3 failures out of 86
  payments, so I left it out of the conclusions.
- **Failures are mostly ours to fix.** Failure reasons about data (wrong account, missing address) point
  at our own vendor records, not the bank. The lower a counterparty's data quality score, the more of its
  payments fail (0.8% at 0.9 and above, 3.9% below 0.8).

## What went wrong
- Typos and SQL order mistakes (a missing comma, `GROUP BY currency code`, `ORDER BY` after `LIMIT`, an
  ambiguous column after a join). Each one was a quick fix once the error message was read.
- The README had an illustrative example in it ("SG -> CN takes 20 hours and fails 3%") that did not come
  from the data. The real numbers are about 39 hours and 1.4%, so I corrected it.

## Next
Save each finished query as a view (`vw_NN_...`) in the `.sql` files, then analyses 5 to 9 (liquidity
forecast, cash concentration, FX exposure, reconciliation and anomaly detection).
