"""Part 1b - raw -> cleaned -> clean database.

Run it:  python -m treasury.pipeline.run --config config/simulation.small.yaml

The flow, one module per stage (fill them in this order):

    extract.py     read the monthly Parquet files from raw/
    dq_checks.py   find bad rows -> a "flags" table (payment_id, check_name)
    clean.py       fix what can be fixed, quarantine what can't
    load.py        write pipeline_payments_cleaned, pipeline_quarantine, pipeline_dq_flags to the clean database
    score.py       compare your flags with data/answer_key/dq_defects.parquet (precision / recall)
    run.py         wires the stages together (already written; it calls your functions)

Explore first in notebooks/01_explore_raw_data.ipynb, then move the code that works into
these modules. The notebook is for finding out; the modules are the product.

The pipeline writes its own tables (pipeline_*) next to the simulator's fact_* tables.
fact_payment is what a perfect pipeline would produce, so score.py can also check how
well your repairs match it.
"""
