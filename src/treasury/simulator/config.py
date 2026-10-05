"""Typed, validated view of config/simulation.yaml.

Cross-field rules live here so a bad config fails fast instead of producing
a dataset that silently cannot support one of the analyses.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

RiskRating = Literal["low", "medium", "high"]
Channel = Literal["API", "H2H_FILE", "PORTAL", "LEGACY_FILE"]


class Currency(BaseModel):
    name: str
    decimals: int = 2
    is_restricted: bool = False
    start_rate_to_sgd: float = Field(gt=0)
    daily_vol: float = Field(ge=0)


class FxConfig(BaseModel):
    mean_reversion: float = Field(ge=0, le=1)
    bank_spread_bps: dict[str, float]


class Country(BaseModel):
    name: str
    region: str
    timezone: str
    risk_rating: RiskRating
    settle_ccy: str


class Bank(BaseModel):
    bank_id: str
    name: str
    country: str
    bic: str = Field(min_length=8, max_length=11)
    is_correspondent: bool


class Entity(BaseModel):
    entity_id: str
    name: str
    country: str
    legal_type: str
    functional_ccy: str
    is_in_house_bank: bool = False
    size: float = Field(default=1.0, gt=0)
    primary_channel: Channel = "API"
    net_bias: float = Field(default=0.0, gt=-1, lt=1)


class InterestConfig(BaseModel):
    credit_rate: dict[str, float]
    debit_rate: dict[str, float]


class AccountsConfig(BaseModel):
    per_entity: tuple[int, int]
    pooled_share: float = Field(ge=0, le=1)
    force_unpooled_entities: list[str] = []
    overdraft_entities: list[str] = []
    overdraft_limit: float = Field(default=0.0, ge=0)
    target_scale: float = Field(default=1.0, gt=0)  # scales account target balances with transaction volume
    interest: InterestConfig


class CounterpartiesConfig(BaseModel):
    count: int = Field(gt=0)
    mix: dict[str, float]
    virtual_account_share: float = Field(ge=0, le=1)
    repeat_offender_suppliers: int = Field(ge=0)
    high_risk_counterparty_share: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _mix_sums_to_one(self) -> CounterpartiesConfig:
        if abs(sum(self.mix.values()) - 1.0) > 1e-6:
            raise ValueError(f"counterparties.mix must sum to 1, got {sum(self.mix.values())}")
        return self


class FlowMix(BaseModel):
    ar_receipt: float = Field(ge=0)
    ap_payment: float = Field(ge=0)
    card_spend: float = Field(ge=0)
    urgent_supplier: float = Field(ge=0)

    @model_validator(mode="after")
    def _sums_to_one(self) -> FlowMix:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"volumes.flow_mix must sum to 1, got {total}")
        return self


class VolumesConfig(BaseModel):
    payments_per_business_day: int = Field(gt=0)
    annual_growth: float
    scheduled_scale: float = Field(default=1.0, gt=0)  # scales payroll, tax and intercompany amounts with volume
    flow_mix: FlowMix


class LedgerConfig(BaseModel):
    opening_days_of_outflow: float = Field(ge=0)
    funding_lookahead_business_days: int = Field(ge=1)
    funding_buffer_share: float = Field(ge=0)
    overdraft_fund_trigger: float = Field(gt=0, le=1)
    overdraft_fund_to: float = Field(ge=0, le=1)
    hq_facility_sgd: float = Field(ge=0)
    topup_rounding_sgd: float = Field(gt=0)
    concentration_weekday: int = Field(ge=0, le=6)
    concentration_trigger: float = Field(gt=1)
    concentration_keep: float = Field(ge=0)


class StreamConfig(BaseModel):
    tick_sim_seconds: int = Field(gt=0)
    default_days: int = Field(gt=0)
    export_every_ticks: int = Field(default=0, ge=0)  # refresh exports every N ticks (0 = only when the stream ends)


class Rail(BaseModel):
    is_instant: bool
    is_batch: bool
    fail_rate: float = Field(ge=0, le=1)
    latency_s: tuple[float, float] | None = None
    cut_off_local: str | None = None
    settle_days: int | None = None
    max_amount_sgd: float | None = None
    hops: tuple[int, int] | None = None
    sanctions_hold_rate: float = 0.0


class ScenariosConfig(BaseModel):
    slow_corridor: tuple[str, str]
    high_failure_corridor: tuple[str, str]
    legacy_file_channel_stp_penalty: float
    late_payer_share: float


class AnomaliesConfig(BaseModel):
    duplicate_payment_rate: float
    structuring_threshold_sgd: float
    structuring_rate: float
    round_amount_rate: float
    burst_rate: float
    high_risk_country_share: float
    new_beneficiary_high_value_rate: float
    off_hours_rate: float


class DataQualityConfig(BaseModel):
    null_purpose_code_rate: float
    bad_currency_code_rate: float
    negative_amount_rate: float
    time_order_violation_rate: float
    resent_file_duplicate_rate: float
    inconsistent_country_name_rate: float


class OutputConfig(BaseModel):
    raw_dir: Path
    clean_db_url: str
    answer_key_dir: Path
    export_dir: Path | None = None   # if set: Excel workbook + CSVs are rebuilt after a backfill and while streaming
    docs_dir: Path | None = None     # if set: dataset card + sample CSVs (committed, previewable on GitHub)


class SimulationConfig(BaseModel):
    seed: int
    start_date: date
    backfill_months: int = Field(ge=1)
    warmup_days: int = Field(default=90, ge=0)
    forward_days: int = Field(default=120, ge=0)
    reporting_currency: str
    speed_multiplier: float = Field(gt=0)
    currencies: dict[str, Currency]
    fx: FxConfig
    countries: dict[str, Country]
    banks: list[Bank]
    entities: list[Entity]
    accounts: AccountsConfig
    counterparties: CounterpartiesConfig
    volumes: VolumesConfig
    ledger: LedgerConfig
    stream: StreamConfig
    rails: dict[str, Rail]
    scenarios: ScenariosConfig
    anomalies: AnomaliesConfig
    data_quality: DataQualityConfig
    output: OutputConfig

    @model_validator(mode="after")
    def _cross_references(self) -> SimulationConfig:
        ccys, ctys = set(self.currencies), set(self.countries)
        entity_ids = {e.entity_id for e in self.entities}
        errors: list[str] = []

        if self.reporting_currency not in ccys:
            errors.append(f"reporting_currency {self.reporting_currency} not in currencies")
        for e in self.entities:
            if e.functional_ccy not in ccys:
                errors.append(f"entity {e.entity_id} functional_ccy {e.functional_ccy} not modelled")
            if e.country not in ctys:
                errors.append(f"entity {e.entity_id} country {e.country} not in countries")
        for code, c in self.countries.items():
            if c.settle_ccy not in ccys:
                errors.append(f"country {code} settle_ccy {c.settle_ccy} not modelled")
        for b in self.banks:
            if b.country not in ctys:
                errors.append(f"bank {b.bank_id} country {b.country} not in countries")
        for eid in self.accounts.force_unpooled_entities + self.accounts.overdraft_entities:
            if eid not in entity_ids:
                errors.append(f"accounts references unknown entity {eid}")
        for rate_table in (self.accounts.interest.credit_rate, self.accounts.interest.debit_rate):
            missing = ccys - set(rate_table)
            if missing:
                errors.append(f"interest rates missing for {sorted(missing)}")
        for pair in (self.scenarios.slow_corridor, self.scenarios.high_failure_corridor):
            if not set(pair) <= ctys:
                errors.append(f"scenario corridor {pair} uses unknown country")
        n_ent = len(self.entities)
        for t in ("intercompany", "employee_group", "tax_authority"):
            if round(self.counterparties.mix.get(t, 0) * self.counterparties.count) < n_ent:
                errors.append(f"counterparties.mix gives fewer {t} than entities ({n_ent})")
        if sum(1 for e in self.entities if e.is_in_house_bank) != 1:
            errors.append("exactly one entity must be the in-house bank (pool header)")
        if errors:
            raise ValueError("Invalid simulation config:\n  - " + "\n  - ".join(errors))
        return self

    @property
    def data_start(self) -> date:
        """First calendar day: includes the invoice warm-up before start_date."""
        return self.start_date - timedelta(days=self.warmup_days)

    @property
    def end_date(self) -> date:
        """Last simulated day (inclusive); payments and FX stop here."""
        return (pd.Timestamp(self.start_date) + pd.DateOffset(months=self.backfill_months)).date() - timedelta(days=1)

    @property
    def calendar_end(self) -> date:
        """Last day in dim_date: covers due dates of invoices still open at end_date."""
        return self.end_date + timedelta(days=self.forward_days)

    @property
    def high_risk_countries(self) -> list[str]:
        return [k for k, c in self.countries.items() if c.risk_rating == "high"]


def load_config(path: str | Path = "config/simulation.yaml") -> SimulationConfig:
    with open(path) as fh:
        return SimulationConfig.model_validate(yaml.safe_load(fh))
