import json
from pathlib import Path

import pytest

from joblens.evals.runner import RunConfig, load_configs, load_samples, run_eval
from joblens.llm.types import ChatResult, Usage

ROOT = Path(__file__).parent.parent
SAMPLES = load_samples(
    ROOT / "data" / "samples" / "vacancies", ROOT / "data" / "samples" / "expected"
)
CONFIG = RunConfig(
    name="fake", provider="ollama", base_url="http://h/v1", model="m", mode="schema"
)


class FakeClient:
    """Answers each call with the next scripted reply."""

    def __init__(self, replies):
        self.replies = list(replies)

    def chat(self, messages, *, temperature=0.0, response_format=None):
        return ChatResult(content=self.replies.pop(0), model="fake", latency_s=0.1)


def test_every_sample_has_valid_ground_truth():
    assert [s.name for s in SAMPLES] == sorted(
        p.stem for p in (ROOT / "data/samples/vacancies").glob("*.txt")
    )


def test_samples_are_split_five_dev_five_holdout():
    splits = [s.split for s in SAMPLES]

    assert splits.count("dev") == 5
    assert splits.count("holdout") == 5


def test_eval_config_in_repo_loads():
    configs = load_configs(ROOT / "evals" / "extraction.toml")

    assert len({c.name for c in configs}) == len(configs)  # unique names


def test_perfect_answers_score_one():
    replies = [s.expected.model_dump_json() for s in SAMPLES]

    result = run_eval(CONFIG, SAMPLES, FakeClient(replies))

    assert result.accuracy == 1.0
    assert result.total("hallucinated") == 0
    assert result.list_f1("skills") == 1.0


def test_for_split_keeps_only_that_split():
    replies = [s.expected.model_dump_json() for s in SAMPLES]
    result = run_eval(CONFIG, SAMPLES, FakeClient(replies))

    holdout = result.for_split("holdout")

    assert {s.split for s in holdout.samples} == {"holdout"}
    assert len(holdout.samples) + len(result.for_split("dev").samples) == len(SAMPLES)


def test_failed_extraction_counts_as_zero():
    replies = ["not json", "still not json"] + [
        s.expected.model_dump_json() for s in SAMPLES[1:]
    ]

    result = run_eval(CONFIG, SAMPLES, FakeClient(replies))

    assert result.failed == 1
    assert result.samples[0].error
    n = len(SAMPLES)
    assert result.accuracy == pytest.approx((n - 1) / n)


def test_hallucinated_salary_is_counted():
    sample = SAMPLES[4]  # 05: only "schaal 11", no amounts
    wrong = sample.expected.model_dump(mode="json") | {
        "salary_min": 4250,
        "salary_max": 4250,
        "salary_period": "month",
    }

    result = run_eval(CONFIG, [sample], FakeClient([json.dumps(wrong)]))

    assert result.total("hallucinated") == 3


def test_api_key_comes_from_named_env_var():
    config = CONFIG.model_copy(update={"api_key_env": "MY_KEY"})

    assert config.settings({"MY_KEY": "sk-1"}).api_key == "sk-1"
    with pytest.raises(ValueError, match="MY_KEY is not set"):
        config.settings({})


def test_cost_uses_input_and_output_prices():
    config = CONFIG.model_copy(
        update={"usd_per_m_input": 1.0, "usd_per_m_output": 10.0}
    )
    replies = [s.expected.model_dump_json() for s in SAMPLES]

    class PricedClient(FakeClient):
        def chat(self, messages, **kwargs):
            result = super().chat(messages, **kwargs)
            return result.model_copy(
                update={
                    "usage": Usage.from_api(
                        {
                            "prompt_tokens": 1000,
                            "completion_tokens": 100,
                            "total_tokens": 1300,
                        }
                    )
                }
            )  # 200 hidden reasoning tokens -> 300 output tokens

    result = run_eval(config, SAMPLES, PricedClient(replies))

    # per sample: 1000 * $1/M + 300 * $10/M = $0.004
    assert result.cost_usd == pytest.approx(0.004 * len(SAMPLES))
    assert result.usd_per_1k_vacancies == pytest.approx(4.0)


def test_cost_is_none_without_prices():
    replies = [s.expected.model_dump_json() for s in SAMPLES]

    assert run_eval(CONFIG, SAMPLES, FakeClient(replies)).cost_usd is None
