"""LLM settings, read from LLM_* environment variables (or a local .env file)."""

import os
from collections.abc import Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

REQUIRED_VARS = ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


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


def load_llm_settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    """Build LLMSettings from env vars. Pass `env` explicitly in tests."""
    if env is None:
        load_dotenv()  # fills os.environ from .env; real env vars win
        env = os.environ

    missing = [name for name in REQUIRED_VARS if not env.get(name)]
    if missing:
        raise ValueError(
            f"Missing LLM settings: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    values = {
        "provider": env["LLM_PROVIDER"],
        "base_url": env["LLM_BASE_URL"],
        "api_key": env["LLM_API_KEY"],
        "model": env["LLM_MODEL"],
    }
    if env.get("LLM_THINKING"):
        # pydantic turns "true"/"false"/"1"/"0" into a real bool
        values["thinking"] = env["LLM_THINKING"]
    return LLMSettings(**values)
