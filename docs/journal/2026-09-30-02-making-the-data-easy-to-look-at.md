# 2026-09-30 · Making the data easy to look at

## Problem
After shrinking the dataset (see entry 01) I could finally open it, but three things were still
awkward: 19 separate CSVs, exports that went stale as soon as new data arrived, and nothing a
visitor to the GitHub repo could look at without cloning and running the simulator.

## What I did
1. **One Excel workbook, one sheet per table** (`data_small/exports/treasury_dataset.xlsx`), with
   an "About" sheet that lists every sheet, its row count and what it holds. A CSV file has no
   sheets, so "a CSV with tabs" really means `.xlsx`.
2. **It refreshes itself.** The exports are rebuilt at the end of every backfill, and during the live
   stream once per simulated day (`stream.export_every_ticks`) and again when the stream ends, so the
   workbook always matches the clean database. The workbook is written to a temp file and then renamed, so
   Excel never sees a half-written file. Cost: about 25 s per refresh, which is why it isn't per tick.
3. **A dataset card for GitHub** (`docs/dataset/dataset-card.md`), inspired by how Kaggle summarises a
   dataset: headline numbers, payments by rail, top corridors, every column explained, and one example
   record per table. Alongside it are 50-row sample CSVs, which GitHub renders as tables in the browser.
4. **A plain-English README section** that explains entities, accounts, currencies and the three kinds
   of payment rail, with a real payment from the data traced step by step.

## Decisions
- **The card is generated, not hand-written**, and column descriptions come from the comments in
  `sql/schema/*.sql`. That keeps the documentation from drifting away from the schema. Columns with no
  comment fall back to a short glossary in `exports.py`.
- **The full workbook is not committed** (about 40 MB, and it changes on every run). Only the small
  card and samples are, since they are enough to preview the data and diff nicely.
- **Answer keys stay out of the samples and the card's examples.** The answer-key labels are what I test my
  own analysis against, so publishing them next to the data would defeat the point.

## Next
Part 1b cleaning pipeline, then the nine SQL analyses.
