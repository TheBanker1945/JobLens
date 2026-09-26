"""What a person wants from their next job, and how it moves a match (7.3).

schema.py is the questionnaire's answers; rerank.py applies the ones a field
can answer to the ranking; prompt.py tells the judge the ones that need
reading. Preferences move vacancies and never remove them.
"""

from joblens.preferences.places import Geo
from joblens.preferences.prompt import for_judge
from joblens.preferences.rerank import Reranked, rerank
from joblens.preferences.schema import (
    PREFERENCES_VERSION,
    Conflict,
    Preferences,
    Seniority,
)

__all__ = [
    "PREFERENCES_VERSION",
    "Conflict",
    "Geo",
    "Preferences",
    "Reranked",
    "Seniority",
    "for_judge",
    "rerank",
]
