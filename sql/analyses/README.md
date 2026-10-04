# SQL analyses: how to work through them

One file per analysis (lesson 8.1). Each file follows the same pattern:

1. **Header**: the business question, the metrics, the chart, and the "so what".
2. **Step 1 (✅ given)**: a base query that gets the right rows, already written and runnable.
   Read every line and make sure you know why each filter is there.
3. **Steps 2+ (✏️ TODO)**: a commented-out skeleton with blanks (`___`) and hints.
   Uncomment it, fill in the blanks, run it, and check the result against the **✔️ expect** line.
4. **Last step**: wrap the finished query in `CREATE VIEW vw_NN_...`. The dashboard reads the
   views, so the numbers always come from the database and never from a copied spreadsheet.

## Order
Do 01 → 04 first (aggregates and window functions), then 06 and 07 (joins across balances,
FX and hedges), then 05, 08 and 09 (SQL builds the dataset, Python finishes the job).

## Rules that apply everywhere
| Rule | SQL | Why |
|---|---|---|
| Exclude intercompany from group totals | `WHERE p.is_intercompany = 0` | An internal transfer is two rows (OUT + IN); counting both inflates totals |
| Successful payments | `status IN ('completed','delayed')` | `delayed` = settled, just late |
| Failed payments | `status IN ('rejected','returned')` | |
| Exclude still-open payments from rates | `status <> 'pending'` | Not failed, not succeeded, yet |
| Hours between timestamps | `(julianday(b) - julianday(a)) * 24` | SQLite stores timestamps as text |
| Money | use `amount_sgd` to compare across currencies | Reporting currency is SGD |

## Percentiles in SQLite
SQLite has no `PERCENTILE_CONT`. Use the rank trick (worked example in `02_cross_border.sql`):
number the rows in order with `ROW_NUMBER()`, count them with `COUNT(*) OVER`, then pick the
row at position `0.9 * count`.

## After each analysis
Write `docs/analyses/NN_name.md`: the key chart, 2–3 numbers, and one sentence:
*"A treasurer would care because ___."*
