# JobLens

An AI assistant for the Dutch job market. JobLens pulls structured data out of
vacancies, matches them to a CV with retrieval-augmented generation (RAG), runs
agents, and makes its tools available to Claude through MCP.

This is a learning and portfolio project: each part is built by hand first, so the
concepts are clear before any framework is introduced.

## Roadmap

1. **LLM fundamentals:** a provider-agnostic client and structured extraction from
   Dutch vacancies, plus a small eval comparing models
2. **Embeddings & RAG from scratch:** semantic search, and CV-to-vacancy matching
   with citations
3. **Tool use & agents:** an agent loop written by hand, then compared with a
   framework
4. **MCP:** a Python MCP server exposing JobLens tools to Claude
5. **Portfolio polish & deploy:** Cloud Run

## Works with any LLM provider

JobLens talks to models through the OpenAI-compatible API. Provider, base URL,
model and API key come from environment variables and are never hardcoded, so the
same code runs against Ollama, LM Studio, OpenRouter, Gemini, DeepSeek, Claude or
OpenAI. The default setup is a local model (Ollama with `qwen3:8b`).

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/TheBanker1945/JobLens.git
cd JobLens
uv sync                  # creates .venv and installs the exact versions from uv.lock
cp .env.example .env     # then fill in your provider settings
mkdir -p data/raw        # local-only folder for personal data (see below)
```

For a local Ollama instance, `.env` looks like this:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen3:8b
```

## Development

```bash
uv run pytest                                  # run the tests
uv run ruff check . && uv run ruff format .    # lint and format
```

## Project layout

```text
src/joblens/     package code
scripts/         small runnable experiments
data/samples/    example vacancies (committed)
data/raw/        scraped or personal data (never committed)
docs/            learning log and notes
tests/           tests
```

## Data & privacy

`data/raw/` is in `.gitignore` and is **never committed**. It holds scraped
vacancies and personal data such as CVs. To stay in line with the GDPR, personal data
is processed with local models by default and is never sent to a cloud provider
unless you configure one.

## License

MIT. See [LICENSE](LICENSE).
