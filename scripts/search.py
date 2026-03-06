#!/usr/bin/env python3
"""
OpenSea – Core Execution Script
================================
Routes users through category-aware intake, calls the selected provider API, and
formats 3-5 personalised product recommendations in strict Markdown.

Usage:
    python3 search.py                          # fully interactive
    python3 search.py --query "MacBook, coding, £1200"
    python3 search.py --provider z.ai --save-key <your_zai_key>
    python3 search.py --provider flock.io --save-key <your_flock_key>

Architecture:
    StateManager          – parses initial prompt; category-aware Q&A state machine
    ProviderClient        – builds payload, calls the selected API, parses JSON response
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
CONFIG_CANDIDATES = [
    ROOT_DIR / "config" / "defaults.json",
    ROOT_DIR / "skills" / "opensea" / "config" / "defaults.json",
    ROOT_DIR.parent / "config" / "defaults.json",
    ROOT_DIR.parent / "skills" / "opensea" / "config" / "defaults.json",
]
PROVIDERS_FILE = ROOT_DIR / "config" / "providers.json"


def _load_env() -> None:
    """Load saved provider environment values from runtime/.env."""
    if load_dotenv is not None and ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


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


def _load_providers() -> dict[str, list[str]]:
    """Read providers.json; return an empty mapping on failure."""
    try:
        with open(PROVIDERS_FILE) as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    providers = data.get("providers", {})
    return providers if isinstance(providers, dict) else {}


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
# 2. ProviderClient – provider-specific API integration
# ---------------------------------------------------------------------------
class ProviderClient:
    """
    Calls the selected chat-completion endpoint with a structured prompt built
    from the gathered context.  Returns a structured list of recommendations.
    """

    PROVIDERS: dict[str, dict[str, str]] = {
        "z.ai": {
            "api_key_env": "ZAI_API_KEY",
            "base_env": "ZAI_API_ENDPOINT",
            "default_base": "https://api.z.ai/api/paas/v4",
            "default_model": "glm-5",
            "success_label": "z.ai",
        },
        "flock.io": {
            "api_key_env": "FLOCK_API_KEY",
            "base_env": "FLOCK_API_ENDPOINT",
            "default_base": "https://api.flock.io/v1",
            "default_model": "qwen3-30b-a3b-instruct-2507",
            "success_label": "flock.io",
        },
    }

    DEFAULT_TIMEOUT = 90
    DEFAULT_RETRY_TIMEOUT = 140
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
        - Include exactly 1 reddit_reviews item per product with a short, realistic quote and real Reddit URL.
        - Include exactly 1 supplier per product, using the cheapest credible option first.
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
        - reddit_reviews: exactly 1 short item.
        - suppliers: exactly 1 item.
        - criteria_ratings: include at most 2 keys.
        - No markdown, no prose outside the JSON object.
    """)

    def __init__(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = DEFAULT_TIMEOUT,
        retry_timeout: int = DEFAULT_RETRY_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        compact_max_tokens: int = DEFAULT_COMPACT_MAX_TOKENS,
    ) -> None:
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        self.provider = provider
        self.provider_meta = self.PROVIDERS[provider]
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

        lines.append(
            "\nPlease provide exactly 3 personalised product recommendations in the exact JSON schema specified."
        )
        return "\n".join(lines)

    def _build_payload(self, user_message: str, *, compact_mode: bool) -> dict[str, Any]:
        """Build a normal or compact completion payload."""
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

    def _build_headers(self) -> dict[str, str]:
        """Build provider-specific request headers."""
        if self.provider == "flock.io":
            return {
                "accept": "application/json",
                "Content-Type": "application/json",
                "x-litellm-api-key": self.api_key,
            }
        return {
            "Accept-Language": "en-US,en",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _request_completion(
        self,
        user_message: str,
        *,
        compact_mode: bool,
        timeout: int,
    ) -> tuple[str, str]:
        """Send one completion request and return content plus finish reason."""
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        payload = self._build_payload(user_message, compact_mode=compact_mode)

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.exceptions.Timeout as exc:
            raise TimeoutError(
                f"[ERROR] Request to {self.provider} timed out after {timeout}s. "
                "Check your internet connection or increase request_timeout_seconds in config/defaults.json."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"[ERROR] Could not connect to {self.provider} API: {exc}")

        if resp.status_code == 401:
            raise RuntimeError(
                f"[ERROR] {self.provider} API returned 401 Unauthorized. "
                f"Check your {self.provider_meta['api_key_env']} is correct and has not expired."
            )
        if resp.status_code == 429:
            raise RuntimeError(
                f"[ERROR] {self.provider} API rate limit exceeded (429). "
                "Please wait a moment and try again."
            )
        if not resp.ok:
            raise RuntimeError(
                f"[ERROR] {self.provider} API returned HTTP {resp.status_code}: {resp.text[:300]}"
            )

        try:
            resp_data = resp.json()
            choice = resp_data["choices"][0]
            content = choice["message"]["content"].strip()
            finish_reason = str(choice.get("finish_reason", "")).strip().lower()
        except (KeyError, IndexError, AttributeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"[ERROR] Unexpected {self.provider} response structure: {exc}\n"
                f"Raw response: {resp.text[:500]}"
            )
        return content, finish_reason

    def _request_with_timeout_retry(
        self,
        user_message: str,
        *,
        compact_mode: bool,
    ) -> tuple[str, str]:
        """Retry once with a longer timeout if the first request times out."""
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
        Fetch recommendations with timeout retry and compact fallback.
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
# Provider and key management helpers
# ---------------------------------------------------------------------------
def _normalise_provider(value: str) -> str:
    """Map user input to a supported provider name."""
    raw = value.strip().lower()
    aliases = {
        "z.ai": "z.ai",
        "zai": "z.ai",
        "z-ai": "z.ai",
        "flock.io": "flock.io",
        "flock": "flock.io",
    }
    return aliases.get(raw, "")


def resolve_provider(cli_provider: str = "") -> str:
    """
    Resolve which provider to use.
    Priority:
      1. CLI argument
      2. OPENSEA_PROVIDER environment variable
      3. Interactive prompt
    """
    _load_env()

    if cli_provider:
        provider = _normalise_provider(cli_provider)
        if provider:
            return provider
        sys.exit("[ERROR] Unsupported provider. Choose z.ai or flock.io.")

    env_provider = _normalise_provider(os.environ.get("OPENSEA_PROVIDER", ""))
    if env_provider:
        return env_provider

    while True:
        try:
            answer = input("[OpenSea] Which provider would you like to use? (z.ai/flock.io)\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[OpenSea] Provider selection cancelled.")
            sys.exit(1)

        provider = _normalise_provider(answer)
        if provider:
            return provider
        print("[OpenSea] Please choose either z.ai or flock.io.\n")


def _provider_env_key(provider: str) -> str:
    """Return the provider-specific API key env var name."""
    return ProviderClient.PROVIDERS[provider]["api_key_env"]


def _provider_label(provider: str) -> str:
    """Return a display label for provider prompts."""
    return ProviderClient.PROVIDERS[provider]["success_label"]


def save_provider(provider: str) -> None:
    """Persist the preferred provider to runtime/.env."""
    env_dir = ROOT_DIR / "runtime"
    env_dir.mkdir(parents=True, exist_ok=True)

    if set_key is not None:
        set_key(str(ENV_FILE), "OPENSEA_PROVIDER", provider)
    else:
        existing = ENV_FILE.read_text() if ENV_FILE.exists() else ""
        if "OPENSEA_PROVIDER" in existing:
            new_lines = [
                f"OPENSEA_PROVIDER={provider}\n" if line.startswith("OPENSEA_PROVIDER=") else line
                for line in existing.splitlines(keepends=True)
            ]
            ENV_FILE.write_text("".join(new_lines))
        else:
            with open(ENV_FILE, "a") as fh:
                fh.write(f"OPENSEA_PROVIDER={provider}\n")


def save_api_key(provider: str, key: str) -> None:
    """Persist the provider-specific API key to runtime/.env without printing it."""
    env_dir = ROOT_DIR / "runtime"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_key = _provider_env_key(provider)

    if set_key is not None:
        set_key(str(ENV_FILE), env_key, key)
        save_provider(provider)
        print(f"{env_key} received ✓  (saved to {ENV_FILE.relative_to(ROOT_DIR)})")
    else:
        existing = ENV_FILE.read_text() if ENV_FILE.exists() else ""
        if env_key in existing:
            new_lines = [
                f"{env_key}={key}\n" if line.startswith(f"{env_key}=") else line
                for line in existing.splitlines(keepends=True)
            ]
            ENV_FILE.write_text("".join(new_lines))
        else:
            with open(ENV_FILE, "a") as fh:
                fh.write(f"{env_key}={key}\n")
        save_provider(provider)
        print(f"{env_key} received ✓  (saved to {ENV_FILE.relative_to(ROOT_DIR)})")


def resolve_api_key(provider: str) -> str:
    """
    Resolve the provider-specific API key from environment or prompt.
    Never prints the key value.
    """
    _load_env()
    env_key = _provider_env_key(provider)
    provider_label = _provider_label(provider)
    key = os.environ.get(env_key, "").strip()

    if not key:
        print(f"Please provide your {provider_label} API Key to continue.")
        try:
            import getpass
            key = getpass.getpass(f"[OpenSea] {env_key}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[OpenSea] Key entry cancelled.")
            sys.exit(1)

        if not key:
            sys.exit(f"[ERROR] {env_key} is required to use OpenSea.")

        save_api_key(provider, key)

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
              python3 search.py --provider z.ai --save-key sk-xxxxxxxxxxxxxxxx
              python3 search.py --provider flock.io --save-key sk-xxxxxxxxxxxxxxxx
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
        help="Securely save the selected provider API key to runtime/.env and exit",
    )
    parser.add_argument(
        "--provider",
        metavar="NAME",
        default="",
        help="Provider to use: z.ai or flock.io",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default="",
        help="Override the selected provider model (default: from config/defaults.json)",
    )
    args = parser.parse_args()

    cfg = _load_config()
    provider = resolve_provider(args.provider)

    # --save-key mode: just persist the key and exit
    if args.save_key:
        save_api_key(provider, args.save_key.strip())
        return

    # Load config defaults
    providers = _load_providers()
    provider_models = providers.get(provider, [])
    provider_meta = ProviderClient.PROVIDERS[provider]
    model_key = "zai_model" if provider == "z.ai" else "flock_model"
    base_key = "zai_api_base" if provider == "z.ai" else "flock_api_base"
    model = args.model or cfg.get(model_key, provider_meta["default_model"])
    base_url = os.environ.get(provider_meta["base_env"], cfg.get(base_key, provider_meta["default_base"]))
    timeout = int(cfg.get("request_timeout_seconds", ProviderClient.DEFAULT_TIMEOUT))
    retry_timeout = int(cfg.get("retry_timeout_seconds", ProviderClient.DEFAULT_RETRY_TIMEOUT))
    max_tokens = int(cfg.get("max_tokens", ProviderClient.DEFAULT_MAX_TOKENS))
    compact_max_tokens = int(cfg.get("compact_max_tokens", ProviderClient.DEFAULT_COMPACT_MAX_TOKENS))
    min_recs = int(cfg.get("min_recommendations", 3))

    if provider_models and model not in provider_models:
        supported = ", ".join(provider_models)
        sys.exit(f"[ERROR] Model '{model}' is not allowed for {provider}. Supported models: {supported}")

    # ── Step 1: API key ───────────────────────────────────────────────
    api_key = resolve_api_key(provider)

    # ── Step 2: Questionnaire state machine ───────────────────────────
    print("\n[OpenSea] Let me help you find the best product for your needs.")
    state = StateManager(initial_prompt=args.query)
    context = state.run_questionnaire()

    # ── Step 3: Call selected provider API ────────────────────────────
    print("\n[OpenSea] Searching for the best options... please wait.\n")
    client = ProviderClient(
        provider=provider,
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
