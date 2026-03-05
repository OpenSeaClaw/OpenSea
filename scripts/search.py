#!/usr/bin/env python3
"""
OpenSea – Core Execution Script
================================
Drives a 5-question questionnaire, calls the z.ai API, and formats
3-5 personalised product recommendations in strict Markdown.

Usage:
    python3 search.py                          # fully interactive
    python3 search.py --query "MacBook, coding, £1200"
    python3 search.py --save-key <your_zai_key>

Architecture:
    StateManager          – parses initial prompt; sequential Q&A state machine
    ZAIClient             – builds payload, calls z.ai API, parses JSON response
    RecommendationFormatter – renders star ratings + supplier links in Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Dependency guard – give a helpful message if packages are missing
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    sys.exit(
        "[ERROR] 'requests' is not installed.\n"
        "        Run: pip install -r requirements.txt"
    )

try:
    from dotenv import load_dotenv, set_key
except ImportError:
    # python-dotenv is optional for loading; we degrade gracefully
    load_dotenv = None  # type: ignore[assignment]
    set_key = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Resolve project root and load .env from runtime/
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
ENV_FILE = ROOT_DIR / "runtime" / ".env"
CONFIG_FILE = ROOT_DIR / "config" / "defaults.json"


def _load_env() -> None:
    """Load ZAI_API_KEY from runtime/.env if not already in environment."""
    if load_dotenv is not None and ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


def _load_config() -> dict[str, Any]:
    """Read defaults.json; return empty dict on failure."""
    try:
        with open(CONFIG_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# 1. StateManager – questionnaire state machine
# ---------------------------------------------------------------------------
class StateManager:
    """
    Manages the sequential questionnaire needed to gather product context.

    Questions (asked only when not already known from the initial prompt):
        Q1 – product / what they're looking for
        Q2 – preferred operating system
        Q3 – primary use case
        Q4 – most important factors  ← drives star-rating selection
        Q5 – approximate budget
    """

    QUESTIONS: list[tuple[str, str]] = [
        (
            "product",
            "What are you looking for today?",
        ),
        (
            "os",
            "Which operating system do you prefer? (e.g., macOS, Windows, Linux, No preference)",
        ),
        (
            "use_case",
            "What do you mainly use the laptop for? (e.g., coding, video editing, gaming, general use)",
        ),
        (
            "factors",
            "What are the most important factors to you? "
            "(e.g., Processing speed, Display quality, Weight, Battery life)",
        ),
        (
            "budget",
            "What is your approximate budget? (e.g., £500, £1000, £1500)",
        ),
    ]

    # Simple keyword heuristics to extract answers from a free-text prompt
    _OS_KEYWORDS: list[str] = ["macos", "mac os", "windows", "linux", "chromeos", "chrome os"]
    _BUDGET_PATTERNS: list[str] = ["£", "$", "€", "usd", "gbp", "eur", "budget", "spend", "price"]
    _FACTOR_KEYWORDS: list[str] = [
        "speed", "performance", "display", "screen", "weight", "portable",
        "battery", "storage", "ram", "memory", "build", "design", "keyboard",
    ]

    def __init__(self, initial_prompt: str = "") -> None:
        self.context: dict[str, str] = {}
        if initial_prompt.strip():
            self._parse_initial_prompt(initial_prompt)

    # ------------------------------------------------------------------
    # Prompt parsing helpers
    # ------------------------------------------------------------------
    def _parse_initial_prompt(self, prompt: str) -> None:
        """
        Attempt to pre-fill context from the user's initial free-text prompt.
        Any field successfully detected is stored so its question is skipped.
        """
        lower = prompt.lower()

        # product – if prompt mentions laptop / device type, treat as Q1 answered
        product_hints = ["laptop", "macbook", "notebook", "pc", "computer",
                         "tablet", "chromebook", "looking for", "need a", "want a"]
        if any(h in lower for h in product_hints):
            self.context["product"] = prompt.strip()

        # os
        for kw in self._OS_KEYWORDS:
            if kw in lower:
                self.context["os"] = kw.title()
                break

        # budget – extract any mention of a currency value
        import re
        budget_match = re.search(
            r"[£$€][\s]?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:gbp|usd|eur|pounds?|dollars?)",
            lower,
        )
        if budget_match:
            self.context["budget"] = budget_match.group(0).strip()

        # factors – collect recognised factor keywords into a comma list
        found_factors = [kw for kw in self._FACTOR_KEYWORDS if kw in lower]
        if found_factors:
            self.context["factors"] = ", ".join(found_factors)

    # ------------------------------------------------------------------
    # Interactive questionnaire
    # ------------------------------------------------------------------
    def run_questionnaire(self) -> dict[str, str]:
        """
        Ask only the questions whose answers are not yet in self.context.
        Returns the completed context dict.
        """
        print()  # blank line for readability
        any_asked = False

        for field, question in self.QUESTIONS:
            if field in self.context:
                # Already known – skip silently
                continue

            any_asked = True
            answer = self._ask(question)
            if answer:
                self.context[field] = answer

        if not any_asked:
            print("[OpenSea] All details gathered from your prompt. Searching now...\n")

        return self.context

    @staticmethod
    def _ask(question: str) -> str:
        """Print question, read answer from stdin; retry if blank."""
        while True:
            try:
                answer = input(f"[OpenSea] {question}\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[OpenSea] Search cancelled.")
                sys.exit(0)

            if answer:
                return answer
            print("[OpenSea] Please provide an answer to continue.\n")


# ---------------------------------------------------------------------------
# 2. ZAIClient – z.ai API integration
# ---------------------------------------------------------------------------
class ZAIClient:
    """
    Calls the z.ai chat-completion endpoint with a structured prompt built
    from the gathered context.  Returns a structured list of recommendations.
    """

    DEFAULT_BASE = "https://api.z.ai/v1"
    DEFAULT_MODEL = "z1"

    # System prompt sent to the model – instructs it to return valid JSON
    SYSTEM_PROMPT = textwrap.dedent("""\
        You are an expert tech-product advisor specialising in laptops and
        personal computers. The user will describe what they need; your job is
        to return 3 to 5 highly relevant product recommendations.

        You MUST respond with ONLY a valid JSON object in exactly this schema
        (no markdown fences, no extra text):

        {
          "recommendations": [
            {
              "name": "<Full product name including model year if known>",
              "os": "<macOS | Windows | Linux | Chrome OS>",
              "description": "<2-3 sentences: why this product suits the user's needs>",
              "criteria_ratings": {
                "<factor name>": <integer 1-5>,
                ...
              },
              "suppliers": [
                {
                  "name": "<Retailer name>",
                  "price": "<Price with currency symbol>",
                  "url": "<Full URL to product listing>",
                  "condition": "<new | refurbished | used>"
                }
              ]
            }
          ]
        }

        Rules:
        - criteria_ratings keys must exactly match the user's stated factors (lower-case).
        - Ratings are integers 1 (worst) to 5 (best) for that criterion.
        - Include at least 2 suppliers per product, listing the cheapest options first.
        - Use real, plausible UK retailers (Currys, Amazon UK, John Lewis, eBay, etc.).
        - All prices must be in the currency implied by the user's budget.
        - Never invent URLs — use realistic, well-formed URLs for each retailer.
        - Descriptions must be grounded in actual product specs.
    """)

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def build_user_message(self, context: dict[str, str]) -> str:
        """
        Convert the collected context dict into a clear natural-language
        message for the model.
        """
        lines = ["The user is looking for product recommendations with the following details:"]
        field_labels = {
            "product": "Looking for",
            "os": "Preferred OS",
            "use_case": "Primary use case",
            "factors": "Most important factors",
            "budget": "Approximate budget",
        }
        for field, label in field_labels.items():
            value = context.get(field, "Not specified")
            lines.append(f"- {label}: {value}")

        lines.append(
            "\nPlease provide 3 to 5 personalised product recommendations in the exact JSON schema specified."
        )
        return "\n".join(lines)

    def fetch_recommendations(self, context: dict[str, str]) -> list[dict[str, Any]]:
        """
        Send the chat-completion request and return the parsed recommendations list.
        Raises RuntimeError with a user-friendly message on failure.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": self.build_user_message(context)},
            ],
            "temperature": 0.3,   # Low temperature for factual, consistent results
            "max_tokens": 4096,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"[ERROR] Request to z.ai timed out after {self.timeout}s. "
                "Check your internet connection or increase request_timeout_seconds in config/defaults.json."
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"[ERROR] Could not connect to z.ai API: {exc}")

        if resp.status_code == 401:
            raise RuntimeError(
                "[ERROR] z.ai API returned 401 Unauthorized. "
                "Check your ZAI_API_KEY is correct and has not expired."
            )
        if resp.status_code == 429:
            raise RuntimeError(
                "[ERROR] z.ai API rate limit exceeded (429). "
                "Please wait a moment and try again."
            )
        if not resp.ok:
            raise RuntimeError(
                f"[ERROR] z.ai API returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        # Extract content from the chat-completion response
        try:
            resp_data = resp.json()
            content = resp_data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"[ERROR] Unexpected z.ai response structure: {exc}\n"
                f"Raw response: {resp.text[:500]}"
            )

        # Parse the JSON the model returned
        return self._parse_model_json(content)

    @staticmethod
    def _parse_model_json(content: str) -> list[dict[str, Any]]:
        """
        Robustly parse the JSON returned by the model.
        Strips accidental markdown code fences if present.
        """
        # Strip common markdown fence wrapping
        if content.startswith("```"):
            lines = content.splitlines()
            # Remove first (```json or ```) and last (```) lines
            content = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"[ERROR] The AI response could not be parsed as JSON: {exc}\n"
                f"Received content:\n{content[:600]}"
            )

        recommendations = data.get("recommendations")
        if not isinstance(recommendations, list) or not recommendations:
            raise RuntimeError(
                "[ERROR] The AI response did not contain a valid 'recommendations' list. "
                "Please try again."
            )

        return recommendations


# ---------------------------------------------------------------------------
# 3. RecommendationFormatter – strict Markdown output
# ---------------------------------------------------------------------------
class RecommendationFormatter:
    """
    Renders a list of recommendation dicts into the required Markdown format.

    Key rule: only display star ratings for the criteria the user actually
    mentioned in their answer to Q4 ("factors").  Other criteria are omitted.
    """

    STAR = "⭐️"

    def __init__(self, factors_answer: str) -> None:
        """
        factors_answer: raw string from Q4, e.g. "Processing speed, Display quality, Weight"
        """
        self.requested_factors = self._normalise_factors(factors_answer)

    @staticmethod
    def _normalise_factors(raw: str) -> list[str]:
        """
        Split the factors answer on commas/semicolons and normalise
        each factor to a lower-case form for matching against API response keys.
        """
        import re
        parts = re.split(r"[,;/]+", raw)
        return [p.strip().lower() for p in parts if p.strip()]

    @staticmethod
    def _stars(rating: int) -> str:
        """Convert integer 1-5 to a star emoji string."""
        rating = max(1, min(5, int(rating)))
        return "⭐️" * rating

    def _match_factor(self, api_key: str) -> str | None:
        """
        Return the user-requested factor that best matches an API response key,
        or None if no match.  Matching is by substring (case-insensitive).
        """
        api_lower = api_key.lower()
        for req in self.requested_factors:
            # Check both directions for partial match
            if req in api_lower or api_lower in req:
                return req
        return None

    def format(
        self,
        recommendations: list[dict[str, Any]],
        context: dict[str, str],
    ) -> str:
        """
        Render recommendations to formatted Markdown string.
        """
        if not recommendations:
            return "_No recommendations were returned. Please try again with different criteria._"

        lines: list[str] = ["Your personalised recommendations:\n"]

        for idx, rec in enumerate(recommendations):
            name = rec.get("name", f"Product {idx + 1}")
            os_label = rec.get("os", "")
            description = rec.get("description", "")
            criteria_ratings: dict = rec.get("criteria_ratings", {})
            suppliers: list[dict] = rec.get("suppliers", [])

            # Product heading
            lines.append(f"**{name}**")

            # OS line always shown
            if os_label:
                lines.append(f"- Operating System: {os_label}")

            # Star ratings – ONLY for factors the user requested
            matched_any = False
            for api_key, rating in criteria_ratings.items():
                matched = self._match_factor(api_key)
                if matched is not None:
                    # Display the factor name in title-case for a clean look
                    display_label = api_key.replace("_", " ").title()
                    lines.append(f"- {display_label}: {self._stars(rating)}")
                    matched_any = True

            if not matched_any and criteria_ratings:
                # Fallback: show all ratings if nothing matched (avoids empty block)
                for api_key, rating in criteria_ratings.items():
                    display_label = api_key.replace("_", " ").title()
                    lines.append(f"- {display_label}: {self._stars(rating)}")

            # Description in italics
            if description:
                lines.append(f"\n*{description}*")

            # Suppliers
            if suppliers:
                lines.append("\nAvailable at below suppliers:")
                for supplier in suppliers:
                    s_name = supplier.get("name", "Unknown")
                    s_price = supplier.get("price", "N/A")
                    s_url = supplier.get("url", "#")
                    s_cond = supplier.get("condition", "new")

                    # Note refurbished/used condition in the label
                    cond_note = f" ({s_cond})" if s_cond.lower() not in ("new", "") else ""
                    lines.append(f"- [{s_price} - {s_name}{cond_note}]({s_url})")
            else:
                lines.append("\n*Supplier information unavailable — search the retailer websites directly.*")

            # Separator between products (not after the last one)
            if idx < len(recommendations) - 1:
                lines.append("\n---")

            lines.append("")  # blank line between entries

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------
def save_api_key(key: str) -> None:
    """Persist ZAI_API_KEY to runtime/.env without printing the key value."""
    env_dir = ROOT_DIR / "runtime"
    env_dir.mkdir(parents=True, exist_ok=True)

    if set_key is not None:
        set_key(str(ENV_FILE), "ZAI_API_KEY", key)
        print(f"ZAI_API_KEY received ✓  (saved to {ENV_FILE.relative_to(ROOT_DIR)})")
    else:
        # Fallback: write manually
        existing = ENV_FILE.read_text() if ENV_FILE.exists() else ""
        if "ZAI_API_KEY" in existing:
            # Replace existing line
            new_lines = [
                f"ZAI_API_KEY={key}\n" if line.startswith("ZAI_API_KEY=") else line
                for line in existing.splitlines(keepends=True)
            ]
            ENV_FILE.write_text("".join(new_lines))
        else:
            with open(ENV_FILE, "a") as fh:
                fh.write(f"ZAI_API_KEY={key}\n")
        print(f"ZAI_API_KEY received ✓  (saved to {ENV_FILE.relative_to(ROOT_DIR)})")


def resolve_api_key() -> str:
    """
    Resolve the ZAI_API_KEY from (in priority order):
      1. ZAI_API_KEY environment variable
      2. runtime/.env file
      3. Interactive prompt to the user
    Never prints the key value.
    """
    _load_env()
    key = os.environ.get("ZAI_API_KEY", "").strip()

    if not key:
        print("Please provide your z.ai API Key to continue.")
        try:
            import getpass
            key = getpass.getpass("[OpenSea] ZAI_API_KEY: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[OpenSea] Key entry cancelled.")
            sys.exit(1)

        if not key:
            sys.exit("[ERROR] ZAI_API_KEY is required to use OpenSea.")

        save_api_key(key)

    return key


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenSea – AI-powered product recommendation skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 search.py
              python3 search.py --query "lightweight MacBook for coding under £1200"
              python3 search.py --save-key sk-xxxxxxxxxxxxxxxx
        """),
    )
    parser.add_argument(
        "--query",
        metavar="TEXT",
        default="",
        help="Pre-fill the questionnaire with a free-text prompt",
    )
    parser.add_argument(
        "--save-key",
        metavar="KEY",
        dest="save_key",
        default="",
        help="Securely save a z.ai API key to runtime/.env and exit",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default="",
        help="Override the z.ai model (default: from config/defaults.json)",
    )
    args = parser.parse_args()

    # --save-key mode: just persist the key and exit
    if args.save_key:
        save_api_key(args.save_key.strip())
        return

    # Load config defaults
    cfg = _load_config()
    model = args.model or cfg.get("zai_model", ZAIClient.DEFAULT_MODEL)
    base_url = os.environ.get("ZAI_API_ENDPOINT", cfg.get("zai_api_base", ZAIClient.DEFAULT_BASE))
    timeout = int(cfg.get("request_timeout_seconds", 30))
    min_recs = int(cfg.get("min_recommendations", 3))

    # ── Step 1: API key ───────────────────────────────────────────────
    api_key = resolve_api_key()

    # ── Step 2: Questionnaire state machine ───────────────────────────
    print("\n[OpenSea] Let me help you find the best product for your needs.")
    state = StateManager(initial_prompt=args.query)
    context = state.run_questionnaire()

    # ── Step 3: Call z.ai API ─────────────────────────────────────────
    print("\n[OpenSea] Searching for the best options... please wait.\n")
    client = ZAIClient(api_key=api_key, base_url=base_url, model=model, timeout=timeout)

    try:
        recommendations = client.fetch_recommendations(context)
    except RuntimeError as exc:
        sys.exit(str(exc))

    # Warn if fewer than minimum returned
    if len(recommendations) < min_recs:
        print(
            f"[WARN] Only {len(recommendations)} recommendation(s) returned "
            f"(minimum expected: {min_recs}). Consider broadening your criteria.\n"
        )

    # ── Step 4: Format and print output ──────────────────────────────
    factors_raw = context.get("factors", "")
    formatter = RecommendationFormatter(factors_answer=factors_raw)
    output = formatter.format(recommendations, context)

    print(output)


if __name__ == "__main__":
    main()
