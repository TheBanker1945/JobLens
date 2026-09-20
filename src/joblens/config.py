"""Model settings per task, read from environment variables (or a local .env file).

Each task has its own prefix, so different tasks can use different providers:
LLM_* for the default chat model, EMBED_* for embeddings. Later a PRIVATE_* prefix
keeps personal data (CVs) on a local model while vacancies go to the cloud.
"""

import os
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

REQUIRED_FIELDS = ("PROVIDER", "BASE_URL", "API_KEY", "MODEL")


class LLMSettings(BaseModel):
    provider: str
    base_url: str
    api_key: str = Field(repr=False)  # never show the key in logs or tracebacks
    model: str
    thinking: bool = False

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return url.rstrip("/")


def load_llm_settings(
    env: Mapping[str, str] | None = None, prefix: str = "LLM"
) -> LLMSettings:
    """Build settings from {prefix}_* env vars. Pass `env` explicitly in tests."""
    if env is None:
        load_dotenv()  # fills os.environ from .env; real env vars win
        env = os.environ

    names = {field: f"{prefix}_{field}" for field in REQUIRED_FIELDS}
    missing = [name for name in names.values() if not env.get(name)]
    if missing:
        raise ValueError(
            f"Missing {prefix} settings: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    values = {field.lower(): env[name] for field, name in names.items()}
    if env.get(f"{prefix}_THINKING"):
        # pydantic turns "true"/"false"/"1"/"0" into a real bool
        values["thinking"] = env[f"{prefix}_THINKING"]
    return LLMSettings(**values)
