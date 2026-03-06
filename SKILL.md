---
name: opensea
description: >
  Help users discover and compare laptops and tech products with personalised
  AI-powered recommendations. Use when users ask to find, compare, or buy a
  laptop (or other product), ask to install/update the OpenSea skill, or want
  product advice. Powered by the flock.io API. Before any workflow, collect the
  FLOCK_API_KEY if it is not already set.
---

# OpenSea Skill

## Mandatory Input Gate (before ANY workflow)

Rules:
- Always check for `FLOCK_API_KEY` before running searches.
- If `FLOCK_API_KEY` is not set in the environment, ask the user:
  > "Please provide your flock.io API Key to continue."
- Accept the key as plain text or in the form `FLOCK_API_KEY=xxx`.
- Save the key to `runtime/.env` via `scripts/search.py --save-key <key>`.
- Never print or log the full key; only confirm: `FLOCK_API_KEY received ✓`
- Keep all prompts and replies in English.

## Install / Update Workflow (after Mandatory Input Gate)

When the user asks to get, install, or update the skill, run in order:

1. `bash install/install.sh`
2. `bash install/install-skill.sh`
3. `bash install/verify-skill-install.sh`

On success, output **exactly**:

```
OpenSea powered by flock.io
```

Immediately after that success line, if the user has not already stated a shopping goal,
ask exactly:

```
What would you like to buy?
```

If the user already included a buying goal earlier in the conversation, skip this follow-up
question and continue directly into the Usage Workflow with that context.

Do NOT show `Install/Path/Revision` in the user-facing reply unless explicitly asked.
Never claim success before `verify-skill-install.sh` passes.

## Usage Workflow (after Mandatory Input Gate)

When the user triggers a product search or recommendation request:

1. Run: `python3 scripts/search.py` (passing any pre-known context as `--query "<text>"`)
2. The script drives the questionnaire interactively and calls the flock.io API.
3. Return the formatted Markdown recommendations exactly as the script outputs them.

The script handles the full state machine:
- Parses the initial prompt to pre-fill any already-known answers.
- Asks only the missing questions (up to 5) sequentially.
- Calls the flock.io API once all context is collected.
- Formats and prints 3–5 recommendations in strict Markdown.

## Health Check

Run `bash scripts/health-check.sh` to verify the environment before usage.

## Guardrails

- Never expose the `FLOCK_API_KEY` in outputs or logs.
- Only use model names from `config/providers.json`.
- Do not fabricate product URLs or prices — the flock.io model must provide real sources.
- Show validation errors clearly if the API call fails.
- If the API returns fewer than 3 recommendations, request a retry with broader criteria.
