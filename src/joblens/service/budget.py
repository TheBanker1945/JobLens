"""How much a tester may spend on JobLens's own model key (7.6).

Mahdi's decision (2026-09-25): testers get a limited free allowance on his
Gemini key, "so that I don't get a big bill from Google", or they bring their
own key and use as much as they want. So two limits, both per calendar month
(UTC), both checked before a paid step starts:

- **per tester**: JOBLENS_TESTER_MONTHLY_USD, default $1.00 -- about 25
  matches of ten vacancies at the measured 0.36 cent a judged vacancy;
- **all testers together**: JOBLENS_OPERATOR_MONTHLY_USD, default $10.00, the
  backstop if many testers arrive at once.

The owner is never stopped (it is his key), and neither is anyone on their own
key. Every paid call is recorded either way (storage `usage`), so a person can
see what JobLens cost them.

**The check uses an estimate; the record is exact.** Before a match starts, its
cost is estimated from the shortlist size; after it ends, the real tokens are
priced and written. A person can have one match running at a time (7.4), so
the most they can overshoot is one match. Not counted: the one "wishlist"
advert a model writes per new CV (about 0.1 cent, cached after) -- its tokens
are not reported by the code that writes it.

**Defence in depth.** This is a limit in JobLens's code. Hosting (7.8) should
also cap the Gemini key itself in Google Cloud (a quota on requests per day),
because a budget alert there only warns and never stops spending.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from joblens.service.errors import ServiceError
from joblens.storage import Database, User

# What a step is estimated to cost before it runs, from measurements
# (CLAUDE.md: a judged vacancy is 0.36 cent; reading a CV about half a cent).
UPLOAD_USD = 0.005
PER_JUDGED_USD = 0.004
WISHLIST_USD = 0.001


def match_estimate(top: int) -> float:
    return WISHLIST_USD + top * PER_JUDGED_USD


class BudgetSpent(ServiceError):
    """The free allowance on JobLens's key is used up for this month."""


@dataclass(frozen=True)
class Budgets:
    tester_monthly_usd: float = 1.00
    operator_monthly_usd: float = 10.00

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Budgets":
        env = env if env is not None else os.environ
        return cls(
            tester_monthly_usd=float(env.get("JOBLENS_TESTER_MONTHLY_USD", 1.00)),
            operator_monthly_usd=float(env.get("JOBLENS_OPERATOR_MONTHLY_USD", 10.00)),
        )


class Usage(BaseModel):
    """What a settings page shows about money."""

    month: str  # "2026-09"
    paid_by: str  # who pays for this person's next call: "operator" or "own"
    joblens_usd: float  # spent on JobLens's key this month
    own_usd: float  # spent on their own key this month (priced models only)
    allowance_usd: float | None  # None: no limit (the owner, or an own key)
    left_usd: float | None


def check(
    database: Database, user: User, paid_by: str, budgets: Budgets, estimate: float
) -> None:
    """Refuse a paid step that would go past an allowance. Say which, and why."""
    if paid_by == "own" or user.is_owner:
        return
    spent = database.spent_this_month(user.id)["operator"]
    if spent + estimate > budgets.tester_monthly_usd:
        raise BudgetSpent(
            f"Your free allowance for this month is used up (${spent:.2f} of "
            f"${budgets.tester_monthly_usd:.2f}). Add your own API key in "
            "settings to keep going; it has no limit."
        )
    everybody = database.spent_this_month()["operator"]
    if everybody + estimate > budgets.operator_monthly_usd:
        raise BudgetSpent(
            "JobLens's free allowance for all testers is used up this month. Add "
            "your own API key in settings to keep going."
        )


def usage(database: Database, user: User, paid_by: str, budgets: Budgets) -> Usage:
    spent = database.spent_this_month(user.id)
    limited = paid_by == "operator" and not user.is_owner
    allowance = budgets.tester_monthly_usd if limited else None
    return Usage(
        month=f"{datetime.now(UTC):%Y-%m}",
        paid_by=paid_by,
        joblens_usd=round(spent["operator"], 4),
        own_usd=round(spent["own"], 4),
        allowance_usd=allowance,
        left_usd=round(max(allowance - spent["operator"], 0.0), 4)
        if allowance is not None
        else None,
    )
