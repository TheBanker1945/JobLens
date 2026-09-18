# JobLens

AI assistant for the Dutch job market: extracts structured data from vacancies,
matches them to a CV (RAG), runs agents, and exposes tools via MCP.
Public repo, portfolio project.

## Purpose

This is a LEARNING + portfolio project. The owner wants to understand every part
conceptually, not just have working code.

## Roles

- Mahdi is the ARCHITECT: he decides what gets built and why, reviews, runs and
  understands it. He does not need to learn to write the code.
- Claude Code is the ENGINEER: it writes all the code.

## How to work with me

- Before implementing, give a short plan: what you'll build, which files, and the
  design choice + one alternative you rejected and why. Wait for my OK.
- After implementing, explain how it works in plain language (data flow, not line
  by line) and tell me how to run and verify it myself.
- Keep changes small: one milestone step at a time.
- Prefer no frameworks until we've built the thing by hand once.
- If something I ask for is a bad idea, say so and explain the trade-off.

## Git (I am learning git — teach as we go)

- One branch per milestone (e.g. feat/1.1-raw-call), merged via a pull request.
- Show and briefly explain each git command you run.
- Suggest when a commit makes sense, with a conventional commit message.
- Never force-push, never rewrite pushed history, never commit directly to main.

## Tech

- Python 3.12, uv, pydantic v2, httpx, openai SDK (as generic OpenAI-compatible
  client), pytest, ruff
- Provider-agnostic LLM layer: provider, model, base_url and api_key come from
  config/env, never hardcoded. Must work with Ollama, LM Studio, OpenRouter,
  Gemini, DeepSeek, Claude and OpenAI.
- Local default: Ollama on Windows, reachable from WSL at http://localhost:11434/v1,
  model qwen3:8b. Reasoning/"thinking" must be switchable (off for extraction).
- Personal data (CVs) goes to local models by default — GDPR.

## Project layout

- src/joblens/ package code
- src/joblens/llm/ provider-agnostic LLM layer
- scripts/ small runnable scripts for experiments
- data/samples/ own example vacancies (committed)
- data/raw/ scraped or personal data (never committed)
- docs/ learning log and notes
- tests/

## Commands

- uv run pytest
- uv run ruff check . && uv run ruff format .

## Rules

- Never commit secrets, .env, or anything from data/raw/.
- Conventional commits: feat:, fix:, chore:, docs:, test:, refactor:
- No new dependency without telling me why it's needed.
