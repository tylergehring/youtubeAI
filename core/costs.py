"""Per-job cost recording. Every external provider call must be recorded here."""

from __future__ import annotations

from filelock import FileLock

from core.config import PriceEntry
from core.jobs import JobPaths
from core.logging import get_logger
from core.models.media import CostRecord, JobCost
from core.providers import Usage
from core.storage import read_model, write_model


def estimate_cost_usd(price: PriceEntry | None, usage: Usage) -> float:
    if price is None:
        return 0.0
    return round(
        usage.tokens_in / 1000 * price.input_per_1k_tokens
        + usage.tokens_out / 1000 * price.output_per_1k_tokens
        + usage.characters / 1000 * price.per_1k_characters
        + usage.requests * price.per_request,
        6,
    )


class CostTracker:
    def __init__(
        self,
        job_id: str,
        paths: JobPaths,
        pricing: dict[str, dict[str, PriceEntry]],
    ) -> None:
        self.job_id = job_id
        self._paths = paths
        self._pricing = pricing

    def record(
        self,
        *,
        stage: str,
        provider: str,
        model: str,
        usage: Usage,
        note: str = "",
    ) -> CostRecord:
        price = self._pricing.get(provider, {}).get(model)
        record = CostRecord(
            job_id=self.job_id,
            stage=stage,
            provider=provider,
            model=model,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            characters=usage.characters,
            requests=usage.requests,
            estimated_cost_usd=estimate_cost_usd(price, usage),
            estimated=True,
            note=note if price else (note + " no pricing configured").strip(),
        )
        get_logger(self.job_id, stage).info(
            "External call: provider=%s model=%s tokens_in=%d tokens_out=%d chars=%d "
            "est_cost_usd=%.6f",
            provider,
            model,
            usage.tokens_in,
            usage.tokens_out,
            usage.characters,
            record.estimated_cost_usd,
        )
        with FileLock(str(self._paths.cost_file) + ".lock", timeout=30):
            cost = self.load()
            cost.records.append(record)
            cost.total_estimated_cost_usd = round(
                sum(r.estimated_cost_usd for r in cost.records), 6
            )
            write_model(self._paths.cost_file, cost)
        return record

    def load(self) -> JobCost:
        if self._paths.cost_file.is_file():
            return read_model(self._paths.cost_file, JobCost)
        return JobCost(job_id=self.job_id)
