# 2026-09-30 · Why I built the dataset, and why I shrank it

## Context
The goal is to understand a Global Payments Solutions environment by simulating one. Before
any analysis, I needed data. This entry records how I got there and one course correction.

## Decision 1: build a simulator instead of finding a dataset
- Real payment data is confidential. Public datasets (fraud sets such as PaySim or the
  credit-card fraud data) have one flat table with a fraud flag. They lack payment rails,
  cut-off times, correspondent hops, failure reasons, bank statements, ledger balances,
  invoices, FX exposure and intercompany flows, which are the things I want to analyse.
- A simulator also gives an **answer key**. I plant known patterns and store the answers
  separately, so I can tell whether an analysis is finding something real or just noise.

## Design rules I set (all in `CLAUDE.md`)
- The lifecycle computes full outcomes. "What is visible as of time T" lives in one place (`asof.py`), so backfill and live stream produce identical data.
- Data-quality defects go into the raw layer only. Business anomalies (duplicates, structuring, bursts) are real money and post to the ledger.
- Answer-key labels never enter the clean database.
- Each engine has its own random stream, so changing one engine doesn't reshuffle the others.

## Build progress
v1 events and lifecycle → v2 event trail, ledger, sweeps, balances → v3 live stream →
v4 anomalies, DQ defects, bank statements, hedges, camt.053 export.

## Decision 2: a much smaller profile
The first full build was about 850k payments, 3.5M events and 1.1M statement lines (~1 GB).
That is too much for a learning project: it cannot be opened in Excel or exported to CSV
for eyeballing. I wanted something I can actually browse.

I added `config/simulation.small.yaml` (~106k payments, 447k events, ~20 s build, 69 MB of
CSV) and kept the full profile for later.

### What broke when I shrank it, and what it taught me
Cutting payments per day from 1,500 to 180 was not just changing one number. The tests that
check each analysis's planted pattern caught these problems:
1. **Idle cash vs overdraft (analysis 6) failed.** Payroll, tax, intercompany amounts and
   account target balances were fixed sizes, so with fewer receipts the whole group burned
   cash and the in-house bank went deeply negative. Fix: two new config fields,
   `volumes.scheduled_scale` and `accounts.target_scale`, that scale those with volume
   (both default to 1.0, so the full profile is unchanged).
2. **Anomaly rates.** Rates that gave a few hundred labels at full size gave near zero at
   small size. I raised the rates so each of the 7 anomaly types still has hundreds of
   labelled examples, while keeping labelled anomalies a small share of payments.
3. **Corridor-size thresholds** in the tests were absolute ("at least 200 payments"), so I
   made them scale with volume. At full size they are still exactly 200.
4. **The failure "Pareto" check (analysis 4) is sensitive to the random seed.** With the
   original seed it narrowly missed 70% at the smaller volume. I changed the small
   profile's seed rather than loosening the check.

The lesson: a planted pattern is a statistical claim, and small samples make statistical
claims noisy. The contract tests are what stopped me from shipping a dataset that looked
fine but had lost the story it was built to tell.

## Next
Part 1b cleaning pipeline (raw → clean, using the DQ labels to score it), then the nine SQL
analyses, dashboards, and the API.
