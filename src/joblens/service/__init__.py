"""What JobLens does, as calls: the layer a script, a web request and a
background job all go through.

Below this line are the pieces (cv/, embeddings/, llm/, storage/); above it is
whoever is asking. A service call takes settings, data and a store, returns
results, and raises only `ServiceError`s -- it never reads .env, prints, or
knows which of its callers it is talking to.
"""

from joblens.service.errors import (
    CVUnreadable,
    ProviderRefused,
    ProviderUnreachable,
    ServiceError,
)
from joblens.service.matching import (
    MatchRequest,
    MatchRun,
    Models,
    Progress,
    Ranked,
    judge,
    judge_version,
    rank,
)

__all__ = [
    "CVUnreadable",
    "MatchRequest",
    "MatchRun",
    "Models",
    "Progress",
    "ProviderRefused",
    "ProviderUnreachable",
    "Ranked",
    "ServiceError",
    "judge",
    "judge_version",
    "rank",
]
