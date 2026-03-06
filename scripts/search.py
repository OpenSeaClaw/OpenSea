#!/usr/bin/env python3
"""
OpenSea – Core Execution Script
================================
Routes users through category-aware intake, calls the flock.io API, and
formats 3-5 personalised product recommendations in strict Markdown.

Usage:
    python3 search.py                          # fully interactive
    python3 search.py --query "MacBook, coding, £1200"
    python3 search.py --save-key <your_flock_key>

Architecture:
    StateManager          – parses initial prompt; category-aware Q&A state machine
    FlockClient           – builds payload, calls flock.io API, parses JSON response
    RecommendationFormatter – renders star ratings + supplier links in Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
ENV_CANDIDATES = [
    ROOT_DIR / "runtime" / ".env",
    ROOT_DIR / "skills" / "opensea" / "runtime" / ".env",
    ROOT_DIR.parent / "runtime" / ".env",
]
CONFIG_CANDIDATES = [
    ROOT_DIR / "config" / "defaults.json",
    ROOT_DIR / "skills" / "opensea" / "config" / "defaults.json",
    ROOT_DIR.parent / "config" / "defaults.json",
    ROOT_DIR.parent / "skills" / "opensea" / "config" / "defaults.json",
]


def _first_existing_path(candidates: list[Path]) -> Path | None:
    """Return the first existing path from candidates, else None."""
    for path in candidates:
        if path.exists():
            return path
    return None


def _display_path(path: Path) -> str:
    """Render a concise path for logs."""
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _env_file_for_write() -> Path:
    """Pick an existing .env location when available, else default path."""
    existing = _first_existing_path(ENV_CANDIDATES)
    return existing if existing is not None else ENV_FILE


def _load_env() -> None:
    """Load FLOCK_API_KEY from runtime/.env if not already in environment."""
    if load_dotenv is None:
        return
    for env_file in ENV_CANDIDATES:
        if env_file.exists():
            load_dotenv(env_file, override=False)


def _load_config() -> dict[str, Any]:
    """Read defaults.json; return empty dict on failure."""
    for config_file in CONFIG_CANDIDATES:
        try:
            with open(config_file) as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
        except json.JSONDecodeError:
            continue
    return {}


# ---------------------------------------------------------------------------
# 1. StateManager – questionnaire state machine
# ---------------------------------------------------------------------------
class StateManager:
    """
    Manages category-aware intake.

    Laptops, phones, and cars use multi-question intake.
    Other product types use a single fallback prompt.
    """

    CATEGORY_QUESTIONS: dict[str, list[tuple[str, str]]] = {
        "laptop": [
            (
                "product",
                "What are you looking for today?",
            ),
            (
                "os",
                "Which operating system do you prefer?",
            ),
            (
                "use_case",
                "What do you mainly use the laptop for?",
            ),
            (
                "factors",
                "What are the most important factors to you? "
                "(e.g. Processing speed, display quality, weight, battery life)",
            ),
            (
                "budget",
                "What is your approximate budget? (e.g. £500, £1000)",
            ),
        ],
        "phone": [
            (
                "product",
                "What phone are you looking for today?",
            ),
            (
                "os",
                "Which phone operating system do you prefer?",
            ),
            (
                "use_case",
                "What do you mainly use the phone for?",
            ),
            (
                "factors",
                "What are the most important factors to you? "
                "(e.g. Camera quality, battery life, performance, screen size)",
            ),
            (
                "budget",
                "What is your approximate budget? (e.g. £300, £800)",
            ),
        ],
        "car": [
            (
                "product",
                "What car are you looking for today?",
            ),
            (
                "fuel_type",
                "Which fuel type do you prefer? (e.g. Petrol, diesel, hybrid, electric)",
            ),
            (
                "use_case",
                "What do you mainly use the car for?",
            ),
            (
                "factors",
                "What are the most important factors to you? "
                "(e.g. Reliability, fuel economy, safety, boot space)",
            ),
            (
                "budget",
                "What is your approximate budget? (e.g. £5000, £15000)",
            ),
        ],
    }
    GENERAL_OPENING_QUESTION: tuple[str, str] = (
        "general_request",
        "What product are you looking for, what matters most, and what is your budget?",
    )

    CATEGORY_KEYWORDS: dict[str, list[str]] = {
        "laptop": ["laptop", "macbook", "notebook", "pc", "computer", "chromebook"],
        "phone": ["phone", "iphone", "android", "smartphone", "mobile", "pixel", "galaxy"],
        "car": ["car", "vehicle", "suv", "sedan", "hatchback", "truck", "ev", "hybrid"],
    }

    # Simple keyword heuristics to extract answers from a free-text prompt
    _OS_KEYWORDS: list[str] = ["macos", "mac os", "windows", "linux", "chromeos", "chrome os"]
    _FACTOR_KEYWORDS: list[str] = [
        "speed", "performance", "display", "screen", "weight", "portable",
        "battery", "storage", "ram", "memory", "build", "design", "keyboard",
        "camera", "range", "reliability", "economy", "safety", "space", "comfort",
    ]
    _USE_CASE_KEYWORDS: list[str] = [
        "coding", "programming", "development", "video editing", "editing",
        "gaming", "general use", "school", "study", "work", "office",
        "design", "3d", "streaming", "browsing", "photography", "commuting",
        "family", "road trips", "daily driving",
    ]
    _FUEL_KEYWORDS: list[str] = ["petrol", "diesel", "hybrid", "electric"]

    def __init__(self, initial_prompt: str = "") -> None:
        self.context: dict[str, str] = {}
        self.category = "other"
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
        self.category = self._detect_category(lower)
        self.context["category"] = self.category

        # product – if prompt mentions a device/product type, treat as answered
        product_hints = ["laptop", "macbook", "notebook", "pc", "computer", "tablet",
                         "chromebook", "phone", "iphone", "android", "car", "vehicle",
                         "looking for", "need a", "want a", "buy"]
        if any(h in lower for h in product_hints):
            self.context["product"] = prompt.strip()

        # os
        for kw in self._OS_KEYWORDS:
            if kw in lower:
                self.context["os"] = kw.title()
                break

        # budget – extract any mention of a currency value
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

        # use_case – store the full prompt if it clearly states how the device will be used
        if any(kw in lower for kw in self._USE_CASE_KEYWORDS):
            self.context["use_case"] = prompt.strip()

        # fuel_type for cars
        for kw in self._FUEL_KEYWORDS:
            if kw in lower:
                self.context["fuel_type"] = kw.title()
                break

        if self.category == "other":
            self.context["general_request"] = prompt.strip()

    def _detect_category(self, lower_prompt: str) -> str:
        """Infer product category from the initial prompt."""
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(keyword in lower_prompt for keyword in keywords):
                return category
        return "other"

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

        if "product" not in self.context and "general_request" not in self.context:
            answer = self._ask("What are you looking for today?")
            self.context["product"] = answer
            inferred_category = self._detect_category(answer.lower())
            self.category = inferred_category
            self.context["category"] = inferred_category
            if inferred_category == "other":
                self.context["general_request"] = answer
            any_asked = True

        questions = self.CATEGORY_QUESTIONS.get(self.category)
        if questions is None:
            questions = [self.GENERAL_OPENING_QUESTION]

        for field, question in questions:
            if field in self.context:
                # Already known – skip silently
                continue

            any_asked = True
            answer = self._ask(question)
            if answer:
                self.context[field] = answer

        if not any_asked:
            print("[OpenSea] All details gathered from your prompt. Searching now...\n")

        if self.category in self.CATEGORY_QUESTIONS and "product" not in self.context:
            self.context["product"] = self.category

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
# 2. FlockClient – flock.io API integration
# ---------------------------------------------------------------------------
class FlockClient:
    """
    Calls the flock.io chat-completion endpoint with a structured prompt built
    from the gathered context.  Returns a structured list of recommendations.
    """

    DEFAULT_BASE = "https://api.flock.io/v1"
    DEFAULT_MODEL = "qwen3-30b-a3b-instruct-2507"
    DEFAULT_TIMEOUT = 90
    DEFAULT_RETRY_TIMEOUT = 120
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_COMPACT_MAX_TOKENS = 2048

    # System prompt sent to the model – instructs it to return valid JSON
    SYSTEM_PROMPT = textwrap.dedent("""\
        You are an expert buying advisor. The user will describe what they
        need; your job is to return exactly 3 highly relevant product
        recommendations for the requested category.

        You MUST respond with ONLY a valid JSON object in exactly this schema
        (no markdown fences, no extra text):

        {
          "recommendations": [
            {
              "name": "<Full product name including model year if known>",
              "os": "<OS/platform/fuel type when relevant, otherwise empty string>",
              "description": "<Exactly one sentence: why this product suits the user's needs>",
              "criteria_ratings": {
                "<factor name>": <integer 1-5>,
                ...
              },
              "reddit_reviews": [
                {
                  "sentiment": "<positive | mixed | negative>",
                  "quote": "<Short Reddit quote about the product>",
                  "url": "<Full Reddit discussion URL>"
                }
              ],
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
        - description must be exactly one short sentence (max 20 words).
        - Include exactly 1 reddit_reviews item per product with a short quote and a real Reddit URL.
        - Include exactly 1 supplier per product with a real URL and realistic price.
        - Use real, plausible UK retailers, dealers, or marketplaces appropriate to the category.
        - All prices must be in the currency implied by the user's budget.
        - Never invent URLs — use realistic, well-formed URLs for each retailer.
        - Descriptions must be grounded in actual product specs.
    """)
    COMPACT_SYSTEM_PROMPT = textwrap.dedent("""\
        Return ONLY valid JSON in the required schema.
        Hard limits:
        - Exactly 3 recommendations.
        - description: max 14 words.
        - reddit_reviews: exactly 1 item with a short quote.
        - suppliers: exactly 1 item.
        - criteria_ratings: include up to 2 keys from user factors.
        - No markdown, no extra text.
    """)

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        retry_timeout: int = DEFAULT_RETRY_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        compact_max_tokens: int = DEFAULT_COMPACT_MAX_TOKENS,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = max(1, int(timeout))
        self.retry_timeout = max(self.timeout, int(retry_timeout))
        self.max_tokens = max(256, int(max_tokens))
        self.compact_max_tokens = max(256, int(compact_max_tokens))

    def build_user_message(self, context: dict[str, str]) -> str:
        """
        Convert the collected context dict into a clear natural-language
        message for the model.
        """
        if context.get("category") == "other":
            general_request = context.get("general_request", context.get("product", "Not specified"))
            lines = [
                "The user is looking for product recommendations in a category outside the standard guided flows.",
                f"- Request: {general_request}",
            ]
            lines.append(
                "\nPlease provide exactly 3 personalised product recommendations in the exact JSON schema specified."
            )
            return "\n".join(lines)

        lines = ["The user is looking for product recommendations with the following details:"]
        field_labels = {
            "category": "Category",
            "product": "Looking for",
            "os": "Preferred OS",
            "fuel_type": "Preferred fuel type",
            "use_case": "Primary use case",
            "factors": "Most important factors",
            "budget": "Approximate budget",
        }
        for field, label in field_labels.items():
            value = context.get(field, "Not specified")
            lines.append(f"- {label}: {value}")

        lines.append("\nPlease provide exactly 3 personalised product recommendations in the exact JSON schema specified.")
        return "\n".join(lines)

    def _build_payload(self, user_message: str, *, compact_mode: bool) -> dict[str, Any]:
        """Build request payload for normal or compact mode."""
        system_prompt = self.COMPACT_SYSTEM_PROMPT if compact_mode else self.SYSTEM_PROMPT
        token_budget = self.compact_max_tokens if compact_mode else self.max_tokens
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "temperature": 0.2,
            "max_tokens": token_budget,
        }

    def _request_completion(
        self,
        user_message: str,
        *,
        compact_mode: bool,
        timeout: int,
    ) -> tuple[str, str]:
        """
        Send one completion request.
        Returns:
          - model content string
          - finish_reason (lower-case)
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "x-litellm-api-key": self.api_key,
        }
        payload = self._build_payload(user_message, compact_mode=compact_mode)

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            raise TimeoutError(
                f"[ERROR] Request to flock.io timed out after {timeout}s. "
                "Check your internet connection or increase request_timeout_seconds in config/defaults.json."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"[ERROR] Could not connect to flock.io API: {exc}")

        if resp.status_code == 401:
            raise RuntimeError(
                "[ERROR] flock.io API returned 401 Unauthorized. "
                "Check your FLOCK_API_KEY is correct and has not expired."
            )
        if resp.status_code == 429:
            raise RuntimeError(
                "[ERROR] flock.io API rate limit exceeded (429). "
                "Please wait a moment and try again."
            )
        if not resp.ok:
            raise RuntimeError(
                f"[ERROR] flock.io API returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            resp_data = resp.json()
            choice = resp_data["choices"][0]
            content = choice["message"]["content"].strip()
            finish_reason = str(choice.get("finish_reason", "")).strip().lower()
        except (KeyError, IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"[ERROR] Unexpected flock.io response structure: {exc}\n"
                f"Raw response: {resp.text[:500]}"
            )
        return content, finish_reason

    def _request_with_timeout_retry(
        self,
        user_message: str,
        *,
        compact_mode: bool,
    ) -> tuple[str, str]:
        """Request once, then retry with a longer timeout if needed."""
        timeouts = [self.timeout]
        if self.retry_timeout > self.timeout:
            timeouts.append(self.retry_timeout)

        if compact_mode and self.retry_timeout > self.timeout:
            timeouts = [self.retry_timeout]

        last_timeout_error: TimeoutError | None = None
        for idx, timeout in enumerate(timeouts):
            try:
                return self._request_completion(
                    user_message,
                    compact_mode=compact_mode,
                    timeout=timeout,
                )
            except TimeoutError as exc:
                last_timeout_error = exc
                if idx < len(timeouts) - 1:
                    print(
                        f"[WARN] Request timed out at {timeout}s; retrying with {timeouts[idx + 1]}s..."
                    )
                    continue
                break

        if last_timeout_error is not None:
            raise RuntimeError(str(last_timeout_error))
        raise RuntimeError("[ERROR] Request failed before completion.")

    def fetch_recommendations(self, context: dict[str, str]) -> list[dict[str, Any]]:
        """
        Fetch recommendations with resilience:
        - timeout retry using a longer timeout
        - fallback compact re-request on truncated outputs
        - fallback compact re-request on JSON parse failure
        """
        user_message = self.build_user_message(context)

        content, finish_reason = self._request_with_timeout_retry(
            user_message,
            compact_mode=False,
        )

        parse_error: RuntimeError | None = None
        try:
            recommendations = self._parse_model_json(content)
        except RuntimeError as exc:
            parse_error = exc
            recommendations = []

        if finish_reason == "length" or parse_error is not None:
            if finish_reason == "length":
                print("[WARN] Model response was truncated (finish_reason=length). Retrying in compact mode...")
            else:
                print("[WARN] Model returned invalid JSON. Retrying in compact mode...")

            compact_content, _ = self._request_with_timeout_retry(
                user_message,
                compact_mode=True,
            )
            try:
                return self._parse_model_json(compact_content)
            except RuntimeError as compact_error:
                if parse_error is not None:
                    raise RuntimeError(
                        f"{parse_error}\n[ERROR] Compact retry failed: {compact_error}"
                    )
                raise RuntimeError(
                    f"[ERROR] Compact retry failed after truncated response: {compact_error}"
                )

        return recommendations

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
    REVIEW_EMOJI = {
        "positive": "😃",
        "mixed": "😕",
        "negative": "😕",
    }

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

    def _format_reddit_reviews(self, reviews: list[dict[str, Any]]) -> list[str]:
        """Render Reddit review snippets as linked quotes."""
        if not reviews:
            return []

        lines = ["", "Reviews from Reddit:"]
        for review in reviews[:2]:
            sentiment = str(review.get("sentiment", "mixed")).strip().lower()
            emoji = self.REVIEW_EMOJI.get(sentiment, "😕")
            quote = str(review.get("quote", "")).strip()
            url = str(review.get("url", "")).strip()
            if not quote:
                continue
            if url:
                lines.append(f'- {emoji}: ["{quote}"]({url})')
            else:
                lines.append(f'- {emoji}: "{quote}"')
        return lines if len(lines) > 2 else []

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
        attribute_label = self._attribute_label(context.get("category", "other"))

        for idx, rec in enumerate(recommendations):
            name = rec.get("name", f"Product {idx + 1}")
            os_label = rec.get("os", "")
            description = rec.get("description", "")
            criteria_ratings: dict = rec.get("criteria_ratings", {})
            reddit_reviews: list[dict] = rec.get("reddit_reviews", [])
            suppliers: list[dict] = rec.get("suppliers", [])

            # Product heading
            lines.append(f"**{name}**")

            # OS line always shown
            if os_label:
                lines.append(f"- {attribute_label}: {os_label}")

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

            lines.extend(self._format_reddit_reviews(reddit_reviews))

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

        lines.append("Please let me know how well these recommendations fit your needs! 🤓")
        return "\n".join(lines)

    @staticmethod
    def _attribute_label(category: str) -> str:
        """Pick the most natural secondary attribute label for the category."""
        if category == "car":
            return "Fuel Type"
        if category == "phone":
            return "Operating System"
        if category == "laptop":
            return "Operating System"
        return "Platform"


# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------
def save_api_key(key: str) -> None:
    """Persist FLOCK_API_KEY to runtime/.env without printing the key value."""
    target_env = _env_file_for_write()
    env_dir = target_env.parent
    env_dir.mkdir(parents=True, exist_ok=True)
    target_display = _display_path(target_env)

    if set_key is not None:
        set_key(str(target_env), "FLOCK_API_KEY", key)
        print(f"FLOCK_API_KEY received ✓  (saved to {target_display})")
    else:
        # Fallback: write manually
        existing = target_env.read_text() if target_env.exists() else ""
        if "FLOCK_API_KEY" in existing:
            # Replace existing line
            new_lines = [
                f"FLOCK_API_KEY={key}\n" if line.startswith("FLOCK_API_KEY=") else line
                for line in existing.splitlines(keepends=True)
            ]
            target_env.write_text("".join(new_lines))
        else:
            with open(target_env, "a") as fh:
                fh.write(f"FLOCK_API_KEY={key}\n")
        print(f"FLOCK_API_KEY received ✓  (saved to {target_display})")


def resolve_api_key() -> str:
    """
    Resolve the FLOCK_API_KEY from (in priority order):
      1. FLOCK_API_KEY environment variable
      2. runtime/.env file
      3. Interactive prompt to the user
    Never prints the key value.
    """
    _load_env()
    key = os.environ.get("FLOCK_API_KEY", "").strip()

    if not key:
        print("Please provide your flock.io API Key to continue.")
        try:
            import getpass
            key = getpass.getpass("[OpenSea] FLOCK_API_KEY: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[OpenSea] Key entry cancelled.")
            sys.exit(1)

        if not key:
            sys.exit("[ERROR] FLOCK_API_KEY is required to use OpenSea.")

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
        help="Securely save a flock.io API key to runtime/.env and exit",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default="",
        help="Override the flock.io model (default: from config/defaults.json)",
    )
    args = parser.parse_args()

    # --save-key mode: just persist the key and exit
    if args.save_key:
        save_api_key(args.save_key.strip())
        return

    # Load config defaults
    cfg = _load_config()
    model = args.model or cfg.get("flock_model", FlockClient.DEFAULT_MODEL)
    base_url = os.environ.get("FLOCK_API_ENDPOINT", cfg.get("flock_api_base", FlockClient.DEFAULT_BASE))
    timeout = int(cfg.get("request_timeout_seconds", FlockClient.DEFAULT_TIMEOUT))
    retry_timeout = int(cfg.get("retry_timeout_seconds", FlockClient.DEFAULT_RETRY_TIMEOUT))
    max_tokens = int(cfg.get("max_tokens", FlockClient.DEFAULT_MAX_TOKENS))
    compact_max_tokens = int(cfg.get("compact_max_tokens", FlockClient.DEFAULT_COMPACT_MAX_TOKENS))
    min_recs = int(cfg.get("min_recommendations", 3))

    # ── Step 1: API key ───────────────────────────────────────────────
    api_key = resolve_api_key()

    # ── Step 2: Questionnaire state machine ───────────────────────────
    print("\n[OpenSea] Let me help you find the best product for your needs.")
    state = StateManager(initial_prompt=args.query)
    context = state.run_questionnaire()

    # ── Step 3: Call flock.io API ─────────────────────────────────────
    print("\n[OpenSea] Searching for the best options... please wait.\n")
    client = FlockClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        retry_timeout=retry_timeout,
        max_tokens=max_tokens,
        compact_max_tokens=compact_max_tokens,
    )

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
