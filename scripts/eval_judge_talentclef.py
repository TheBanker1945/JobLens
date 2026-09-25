"""Does the judge agree with human experts it has never met? TalentCLEF 2026.

    uv run python scripts/eval_judge_talentclef.py
    uv run python scripts/eval_judge_talentclef.py --temperature 1
    uv run python scripts/eval_judge_talentclef.py --limit 20     # a cheap look

Every other judge number in this repo is measured against labels that Claude
wrote (the four invented CVs) or that one person wrote (Mahdi's CV). These are
neither. TalentCLEF 2026 Task A publishes English job descriptions and résumés
whose pairs were "annotated by human experts, who determined whether each
résumé is suitable for a given job offer" (CC-BY 4.0, Zenodo
10.5281/zenodo.17625261; Fabregat et al. 2026, Gasco et al. 2026). The sample
read here is 200 pairs over the ten development job descriptions, drawn with a
fixed seed; `data/raw/talentclef/README.md` says how.

What the label is decides what this can say:

- **Binary, and lenient.** A 1 reads as "belongs in this job's candidate pool":
  a part-time shop assistant is a 1 for a sales director. It is nearer our
  `possible` than our `strong`, so a verdict counts as a yes when it is not
  `weak`.
- **Set per job.** One annotator's bar for a cashier is not another's for an
  HVAC engineer, so the unit is one job description: concordance within it,
  printed per job, never pooled across them.
- **A recruiter's question.** It asks whether the candidate is suitable, not
  whether they should apply. So this is not a measure of the thing 6.2 changed
  -- it is the guard on it: after years and degrees stopped deciding the
  verdict, can the judge still tell a candidate who fits from one who does not?
- **Synthetic English.** Nothing here says how the judge reads a Dutch advert.

The data lives in data/raw/talentclef/ (not committed). Answers are stored in
data/cache/talentclef-judgements.json under the same names as the judge eval's.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import httpx
import openai
from dotenv import load_dotenv

from joblens.config import load_llm_settings
from joblens.cv.clean import redact_cv
from joblens.cv.documents import QueryPart
from joblens.cv.judge import PROMPT_VERSION, Verdict
from joblens.cv.match import CVMatch
from joblens.cv.store import CVCache
from joblens.evals.judging import JudgeVariant, concordance
from joblens.llm.client import LLMClient
from joblens.llm.pricing import cost_usd, format_cost
from joblens.llm.structured import default_mode
from joblens.sources.base import Vacancy

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "raw" / "talentclef"
DEV = DATA / "TaskA" / "development" / "en"
CACHE = ROOT / "data" / "cache" / "talentclef-judgements.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--thinking", choices=["on", "off"])
    parser.add_argument("--sample", type=int, default=1)
    parser.add_argument("--fresh", action="store_true", help="ignore stored answers")
    parser.add_argument("--limit", type=int, help="only the first N pairs")
    args = parser.parse_args()

    if not (DATA / "sample.json").exists():
        print(f"No sample in {DATA}. See data/raw/talentclef/README.md.")
        return 1
    sample = json.loads((DATA / "sample.json").read_text(encoding="utf-8"))
    rows = sample["pairs"][: args.limit]

    load_dotenv()
    settings = load_llm_settings(prefix="CV")
    if args.thinking:
        settings = settings.model_copy(update={"thinking": args.thinking == "on"})
    variant = JudgeVariant(args.temperature, settings.thinking, args.sample)
    print(
        f"{len(rows)} TalentCLEF pairs over {len({r['jd_id'] for r in rows})} job "
        f"descriptions, judged by {settings.model}, prompt {PROMPT_VERSION}, "
        f"{variant.describe()}"
    )

    pairs, labels = [], {}
    vacancies: dict[str, Vacancy] = {}
    for row in rows:
        vacancy = vacancies.get(row["jd_id"]) or _vacancy(row["jd_id"])
        vacancies[row["jd_id"]] = vacancy
        resume = (DEV / "corpus" / row["resume_id"]).read_text(encoding="utf-8")
        # Only what the task needs, as for a real CV: these résumés are
        # synthetic, but they carry addresses and phone numbers all the same.
        cv_text = redact_cv(resume).text
        match = CVMatch(vacancy, 0.0, "", QueryPart("talentclef", ""))
        pairs.append((cv_text, match))
        labels[id(match)] = (row["jd_id"], row["grade"])

    try:
        with LLMClient(settings, max_retries=5) as client:
            judged, failures, paid = variant.judge(
                pairs,
                client,
                CVCache(CACHE),
                model=settings.model,
                mode=default_mode(settings),
                fresh=args.fresh,
            )
    except (httpx.ConnectError, openai.APIConnectionError) as err:
        print(f"Cannot reach a server: {err}")
        return 1
    for failure in failures:
        print(f"  failed: {failure}")
    print(f"  {len(judged)} judged ({paid} new)")

    by_job: dict[str, list[tuple[int, int, bool]]] = defaultdict(list)
    for one in judged:
        jd_id, grade = labels[id(one.match)]
        said_yes = one.judgement.verdict is not Verdict.WEAK
        by_job[jd_id].append((one.judgement.fit, grade, said_yes))

    print_jobs(by_job, vacancies)
    quotes = sum(one.quotes for one in judged)
    dropped = sum(len(one.dropped) for one in judged)
    print(
        f"\n  quotes: {quotes}, found in the source: "
        f"{(quotes - dropped) / quotes if quotes else 1:.0%} ({dropped} dropped)"
    )
    tokens_in = sum(one.prompt_tokens for one in judged)
    tokens_out = sum(one.output_tokens for one in judged)
    cost = cost_usd(settings, tokens_in, tokens_out)
    print(f"  {tokens_in} tokens in, {tokens_out} out  |  {format_cost(cost)} this run")
    return 0


def print_jobs(by_job, vacancies: dict[str, Vacancy]) -> None:
    """Per job description: concordance, and how often the judge said yes."""
    print("\n===== the judge against the experts, per job description =====")
    print(
        f"{'job':<36} {'pairs':>5} {'1s':>4}  {'concordance':>11}  "
        f"{'yes when 1':>10}  {'yes when 0':>10}"
    )
    scores, yes_1, yes_0 = [], [], []
    for jd_id in sorted(by_job, key=lambda jd: vacancies[jd].title):
        rows = by_job[jd_id]
        agree = concordance((fit, grade) for fit, grade, _ in rows)
        ones = [yes for _, grade, yes in rows if grade]
        zeros = [yes for _, grade, yes in rows if not grade]
        yes_1 += ones
        yes_0 += zeros
        if agree is not None:
            scores.append(agree)
        print(
            f"{vacancies[jd_id].title[:36]:<36} {len(rows):>5} {len(ones):>4}  "
            f"{_share(agree):>11}  {_rate(ones):>10}  {_rate(zeros):>10}"
        )
    if scores:
        print(
            f"\n  concordance per job: lowest {min(scores):.2f}, median "
            f"{sorted(scores)[len(scores) // 2]:.2f}, over {len(scores)} jobs"
        )
    print(
        f"  said yes (strong or possible) to {_rate(yes_1)} of the experts' 1s and "
        f"{_rate(yes_0)} of their 0s"
    )
    print(
        "\n  A 1 is lenient ('belongs in the pool') and set per job, so the number "
        "to read is\n  the concordance within each job, not one threshold across "
        "them."
    )


def _vacancy(jd_id: str) -> Vacancy:
    text = (DEV / "queries" / jd_id).read_text(encoding="utf-8")
    return Vacancy(
        source="talentclef",
        source_id=jd_id,
        url="",
        title=text.strip().splitlines()[0].strip(),
        text=text,
    )


def _share(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _rate(flags: list[bool]) -> str:
    return f"{sum(flags)}/{len(flags)}" if flags else "-"


if __name__ == "__main__":
    sys.exit(main())
