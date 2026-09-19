import json
from pathlib import Path

import pytest

from joblens.evals.runner import RunConfig, load_configs, load_samples, run_eval
from joblens.llm.types import ChatResult

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


def test_eval_config_in_repo_loads():
    configs = load_configs(ROOT / "evals" / "extraction.toml")

    assert len({c.name for c in configs}) == len(configs)  # unique names


def test_perfect_answers_score_one():
    replies = [s.expected.model_dump_json() for s in SAMPLES]

    result = run_eval(CONFIG, SAMPLES, FakeClient(replies))

    assert result.accuracy == 1.0
    assert result.total("hallucinated") == 0
    assert result.list_f1("skills") == 1.0


def test_failed_extraction_counts_as_zero():
    replies = ["not json", "still not json"] + [
        s.expected.model_dump_json() for s in SAMPLES[1:]
    ]

    result = run_eval(CONFIG, SAMPLES, FakeClient(replies))

    assert result.failed == 1
    assert result.samples[0].error
    assert result.accuracy == pytest.approx(4 / 5)


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
