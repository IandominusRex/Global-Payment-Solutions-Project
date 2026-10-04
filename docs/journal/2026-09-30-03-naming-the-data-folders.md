# 2026-09-30 · Naming the data folders so they can't be misread

## Problem
The data folders used industry jargon: `landing/`, `warehouse/`, `truth/`. When I started the cleaning
step I couldn't tell from the names which folder was dirty, which was correct, and which I was allowed
to look at. If the author is confused, a reader will be too.

## What I did
Renamed the folders, config keys, code and docs so each name says what the folder is:

| Old | New | Meaning |
|---|---|---|
| `landing/` | `raw/` | Files as received. The payments files contain planted defects. |
| `warehouse/` | `clean/` | The clean database, `treasury.sqlite`. Industry term: the data warehouse. |
| `truth/` | `answer_key/` | What was planted. Only scoring code may read it. |

The answer-key files were renamed too, because `label_dq` or `statement_payment` don't say what is
inside: `dq_defects`, `business_anomalies`, `payment_to_invoice`, `statement_line_to_payment`,
`payment_business_flow`. The cleaning pipeline's own tables are prefixed `pipeline_`, so it's
obvious which tables I produced and which the simulator did.

## Decisions
- **"clean", not "true" or "accurate".** The database will also hold my pipeline's output, which
  may contain mistakes, so calling it "true" would overclaim. It would also clash with the answer key,
  which is the actual truth.
- **Keep the industry terms once.** The README defines `clean/` as "the data warehouse" and the answer
  key as "ground truth", so I still know the words used in interviews.
- **Rename everywhere, not just the folders.** A half-rename (`clean/` folder but a `warehouse_url`
  config key) leaves the same doubt the rename was meant to remove.
