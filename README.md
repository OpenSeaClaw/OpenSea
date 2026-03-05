# OpenSea

OpenSea is an OpenClaw-ready skill that gives users **personalised laptop and
tech-product recommendations** powered by the [z.ai](https://z.ai) API.

---

## Features

- **Smart questionnaire** – asks only the questions not already answered in the
  user's prompt (OS, use-case, important factors, budget).
- **z.ai integration** – uses the z.ai chat-completion API to surface 3–5
  highly relevant recommendations with real supplier pricing.
- **Strict Markdown output** – star ratings shown only for the criteria the
  user actually cares about; supplier links included per product.
- **Secure key handling** – API key stored locally in `runtime/.env`, never
  printed back to the user.

---

## Quick Start

```bash
cd /Users/archie/project/OpenSea

# 1. Install (chmod scripts, create runtime dir)
bash install/install.sh

# 2. Verify dependencies
bash install/verify.sh

# 3. Install the skill into the OpenClaw workspace
bash install/install-skill.sh

# 4. Verify the skill is correctly installed
bash install/verify-skill-install.sh
```

### Set your z.ai API Key

```bash
export ZAI_API_KEY="your_zai_api_key_here"
# Or let the script save it for you:
python3 scripts/search.py --save-key "your_zai_api_key_here"
```

### Run a search

```bash
# Interactive (questionnaire driven)
python3 scripts/search.py

# Pre-fill context from the command line
python3 scripts/search.py --query "I need a lightweight MacBook for coding under £1200"
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ZAI_API_KEY` | **Yes** | z.ai API key for chat completions |
| `ZAI_API_ENDPOINT` | No | Override API base URL (default: `https://api.z.ai/v1`) |
| `OPENCLAW_WORKSPACE` | No | Override workspace root for skill install target |

---

## Project Layout

```
OpenSea/
├── SKILL.md                     # OpenClaw agent skill descriptor
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── config/
│   ├── defaults.json            # Tunable defaults (timeouts, result counts)
│   └── providers.json           # z.ai model whitelist
├── install/
│   ├── install.sh               # Bootstrap: chmod + runtime dir
│   ├── verify.sh                # Dependency check
│   ├── install-skill.sh         # Copy skill/ into OpenClaw workspace
│   ├── verify-skill-install.sh  # Verify the installed SKILL.md
│   ├── uninstall.sh             # Remove installed skill
│   └── rollback.sh              # Restore most-recent backup
├── scripts/
│   ├── health-check.sh          # Environment readiness check
│   └── search.py                # Core: state machine + API + formatter
├── skill/
│   └── opensea/                 # Installable skill subtree (mirrors root)
├── tests/
│   └── sample_request.json      # Example API payload for manual testing
└── dist/
    └── opensea.skill            # Packaged skill artifact
```

---

## Rebuilding the Packaged Skill

```bash
python3 -m venv .venv
.venv/bin/pip install pyyaml
.venv/bin/python ../openclaw/skills/skill-creator/scripts/package_skill.py ./skill/opensea ./dist
```

---

## Notes

- Python 3.8+ is required for `scripts/search.py`.
- The skill uses `requests` and `python-dotenv`; install via `pip install -r requirements.txt`.
- For production deployments, move the execution layer to a dedicated OpenClaw plugin tool.
