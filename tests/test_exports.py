import re
import zipfile

import pandas as pd
import pytest

from treasury.simulator.backfill import run_backfill
from treasury.simulator.config import SimulationConfig
from treasury.simulator.sinks.exports import SAMPLE_ROWS


@pytest.fixture(scope="module")
def exported(cfg, tmp_path_factory) -> SimulationConfig:
    tmp = tmp_path_factory.mktemp("exports")
    raw = cfg.model_dump()
    raw["backfill_months"] = 2
    raw["volumes"]["payments_per_business_day"] = 100
    raw["output"] = {"landing_dir": tmp / "landing", "truth_dir": tmp / "truth", "export_dir": tmp / "exports",
                     "docs_dir": tmp / "docs", "warehouse_url": f"sqlite:///{tmp / 'wh.sqlite'}"}
    out = SimulationConfig.model_validate(raw)
    run_backfill(out, log=lambda *_: None)
    return out


def test_workbook_has_one_sheet_per_table(exported):
    z = zipfile.ZipFile(exported.output.export_dir / "treasury_dataset.xlsx")
    sheets = re.findall(r'sheet name="([^"]+)"', z.read("xl/workbook.xml").decode())
    assert sheets[0] == "About"
    assert {"fact_payment", "fact_payment_event", "dim_entity", "fact_balance"} <= set(sheets)
    assert len(sheets) == 1 + len(list((exported.output.export_dir / "csv").glob("*.csv")))


def test_dataset_card_and_samples(exported):
    card = (exported.output.docs_dir / "dataset-card.md").read_text()
    assert "## At a glance" in card and "### `fact_payment`" in card
    sample = pd.read_csv(exported.output.docs_dir / "samples" / "fact_payment.csv")
    assert 0 < len(sample) <= SAMPLE_ROWS
    assert "truth" not in "".join(p.name for p in (exported.output.docs_dir / "samples").glob("*"))
