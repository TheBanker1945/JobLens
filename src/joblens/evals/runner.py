"""Run one eval setting (model + thinking + mode) over all samples; collect scores."""

import json
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel

from joblens.config import LLMSettings
from joblens.evals.scoring import SampleScore, score_details
from joblens.extraction.extract import ExtractionError, Mode, extract_vacancy
from joblens.extraction.schema import VacancyDetails
from joblens.llm.types import ChatClient


class RunConfig(BaseModel):
    """One [[run]] from the eval config file."""

    name: str
    provider: str
    base_url: str
    model: str
    thinking: bool = False
    mode: Mode = "schema"
    api_key_env: str | None = None  # name of the env var holding the key, never the key
    # USD per 1 million tokens, from the provider's pricing page (0 for local models).
    # Output price covers reasoning tokens too.
    usd_per_m_input: float | None = None
    usd_per_m_output: float | None = None

    def settings(self, env: Mapping[str, str] = os.environ) -> LLMSettings:
        api_key = "unused"  # local servers like Ollama ignore the key
        if self.api_key_env:
            api_key = env.get(self.api_key_env, "")
            if not api_key:
                raise ValueError(f"Run {self.name!r}: {self.api_key_env} is not set")
        return LLMSettings(
            provider=self.provider,
            base_url=self.base_url,
            api_key=api_key,
            model=self.model,
            thinking=self.thinking,
        )


Split = Literal["dev", "holdout"]


class Sample(BaseModel):
    name: str
    split: Split  # dev: used for prompt tuning; holdout: only for measuring
    text: str
    expected: VacancyDetails
    alternatives: dict[str, list[Any]] = {}


class SampleResult(BaseModel):
    sample: str
    score: SampleScore | None = None  # None when extraction failed
    error: str | None = None
    predicted: dict[str, Any] | None = None
    attempts: int
    prompt_tokens: int = 0
    output_tokens: int = 0  # answer + reasoning
    latency_s: float = 0.0


class RunResult(BaseModel):
    config: RunConfig
    samples: list[SampleResult]

    @property
    def scored(self) -> list[SampleScore]:
        return [s.score for s in self.samples if s.score]

    @property
    def failed(self) -> int:
        return sum(s.score is None for s in self.samples)

    @property
    def accuracy(self) -> float:
        # A failed extraction gives the user nothing, so it counts as 0.
        return sum(s.accuracy for s in self.scored) / len(self.samples)

    def total(self, outcome: str) -> int:
        return sum(s.count(outcome) for s in self.scored)

    @property
    def cost_usd(self) -> float | None:
        c = self.config
        if c.usd_per_m_input is None or c.usd_per_m_output is None:
            return None
        tokens_in = sum(s.prompt_tokens for s in self.samples)
        tokens_out = sum(s.output_tokens for s in self.samples)
        return (tokens_in * c.usd_per_m_input + tokens_out * c.usd_per_m_output) / 1e6

    @property
    def usd_per_1k_vacancies(self) -> float | None:
        cost = self.cost_usd
        return None if cost is None else cost / len(self.samples) * 1000

    def list_f1(self, field: str) -> float:
        f1s = [ls.f1 for s in self.scored for ls in s.lists if ls.field == field]
        return mean(f1s) if f1s else 0.0


def load_configs(path: Path) -> list[RunConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [RunConfig.model_validate(run) for run in data["run"]]


def load_samples(vacancies_dir: Path, expected_dir: Path) -> list[Sample]:
    samples = []
    for text_path in sorted(vacancies_dir.glob("*.txt")):
        label = json.loads(
            (expected_dir / f"{text_path.stem}.json").read_text(encoding="utf-8")
        )
        samples.append(
            Sample(
                name=text_path.stem,
                split=label["split"],
                text=text_path.read_text(encoding="utf-8"),
                expected=VacancyDetails.model_validate(label["details"]),
                alternatives=label.get("alternatives", {}),
            )
        )
    return samples


def run_eval(config: RunConfig, samples: list[Sample], client: ChatClient) -> RunResult:
    results = []
    for sample in samples:
        try:
            extraction = extract_vacancy(sample.text, client, mode=config.mode)
        except ExtractionError as err:
            results.append(
                SampleResult(
                    sample=sample.name,
                    error=str(err),
                    attempts=len(err.attempts),
                )
            )
            continue
        results.append(
            SampleResult(
                sample=sample.name,
                score=score_details(
                    sample.expected, extraction.details, sample.alternatives
                ),
                predicted=extraction.details.model_dump(mode="json"),
                attempts=extraction.attempts,
                prompt_tokens=extraction.prompt_tokens,
                output_tokens=extraction.output_tokens,
                latency_s=extraction.latency_s,
            )
        )
    return RunResult(config=config, samples=results)
