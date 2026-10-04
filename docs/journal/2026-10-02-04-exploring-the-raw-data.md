# 2026-10-02 · Exploring the raw data before writing the cleaning pipeline

## Problem
The raw files contain planted defects, but I didn't know what they were or how to fix them. Writing
the cleaning pipeline first would have meant guessing. I wanted to find each defect by hand, decide
how to fix it, and only then move the code into the pipeline.

## What I did
Worked through one notebook, `notebooks/01_explore_raw_data.ipynb`:
1. Loaded all the monthly payment files into one table (106,621 rows).
2. Checked every column with nulls and asked: is this expected, or a defect? Only `purpose_code` was a
   real defect. The others (`uetr`, `failure_reason`, `settled_ts`, ...) are empty for good reasons.
3. Hunted the six defects, then opened the answer key to check my work.

| Defect | Rows | Fix |
|---|---|---|
| Duplicate rows (a re-sent file) | 415 | Drop the copy, keep the first |
| Currency code misspelled (`usd`, `US$`, `EUR `) | 585 | Map to the real code |
| Country name misspelled (`China`, `UK`, `de`) | 1,033 | Map to the real code |
| Negative amount | 293 | Take the absolute value, `direction` already says OUT or IN |
| `settled_ts` before `initiated_ts` | 536 | Use the time of the SETTLED event |
| Missing `purpose_code` | 3,158 | Can't fix. Keep the row and flag it |

My counts matched the answer key on all six.

## Decisions
- **Use `deduped` after the duplicates are removed.** The copies are exact repeats, so counting them
  would count every other defect twice.
- **Check that a fix is right, not just valid.** My first currency map made every code a real code,
  but it was wrong for most rows. A valid code is not the same as the correct code. I now compare
  every map to the clean database before I trust it.
- **Don't guess the purpose code.** Even the best guess (the counterparty's usual code) is wrong about
  4 times in 10. Blocking the payment would hide real money, so the row is kept with a flag.
- **Simplified the simulator.** The currency defect used to swap in a random currency, so the only way
  to recover it was to work out the exchange rate from `amount_sgd`. That was too complicated for an
  exploratory project. Now a bad currency code is always a misspelling of the real one, the same as the
  country names, so one map fixes it. The number of defects didn't change.

## Next
Skipping the cleaning pipeline. This project is about payments analytics, and writing the pipeline would
only be practice at data cleaning. The notebook already showed me what dirty data looks like, which is
enough. The SQL analyses never read the pipeline's output anyway, they run on the clean database, so
nothing else depends on it. I left `src/treasury/pipeline/` in the repo, unfinished.

Next is the SQL analyses (`sql/analyses/01` to `09`), starting with 01 money movement.
