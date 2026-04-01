"""Claude API client for Nexus Forge.

Uses Anthropic's Claude API for high-quality schema generation
during the one-time folder onboarding process.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-opus-4-6"
MAX_TOKENS = 4096


@dataclass(frozen=True)
class ClaudeConfig:
    """Configuration for the Claude API."""

    api_key: str
    model: str = DEFAULT_MODEL
    max_tokens: int = MAX_TOKENS

    @classmethod
    def from_external(cls, config_dir: Path) -> ClaudeConfig | None:
        """Load Claude config from external.json. Returns None if no key configured."""
        external_path = config_dir / "external.json"
        if not external_path.is_file():
            return None
        try:
            data = json.loads(external_path.read_text())
            claude = data.get("claude", {})
            api_key = claude.get("api_key", "")
            if not api_key:
                return None
            return cls(
                api_key=api_key,
                model=claude.get("model", DEFAULT_MODEL),
                max_tokens=claude.get("max_tokens", MAX_TOKENS),
            )
        except (json.JSONDecodeError, OSError):
            return None

    @property
    def is_configured(self) -> bool:
        """Check if the API key is set."""
        return bool(self.api_key)


@dataclass
class ClaudeResponse:
    """Response from Claude API."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    success: bool
    error: str | None = None


class ClaudeClient:
    """Client for Anthropic Claude API."""

    def __init__(self, config: ClaudeConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=120)

    def infer(self, system_prompt: str, user_message: str) -> ClaudeResponse:
        """Send a request to Claude API.

        Args:
            system_prompt: System instructions.
            user_message: User content to process.

        Returns:
            ClaudeResponse with the raw content and metadata.
        """
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }

        start_ms = time.monotonic() * 1000

        try:
            response = self._client.post(CLAUDE_API_URL, headers=headers, json=payload)
            latency_ms = (time.monotonic() * 1000) - start_ms

            if response.status_code != 200:
                error_msg = f"Claude API returned HTTP {response.status_code}: {response.text[:500]}"
                logger.error(error_msg)
                return ClaudeResponse(
                    content="",
                    model=self._config.model,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    success=False,
                    error=error_msg,
                )

            body = response.json()
            content = body["content"][0]["text"]
            usage = body.get("usage", {})

            logger.info(
                "Claude inference completed in %.0fms (%d in / %d out tokens)",
                latency_ms,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
            )

            return ClaudeResponse(
                content=content,
                model=body.get("model", self._config.model),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=latency_ms,
                success=True,
            )

        except httpx.TimeoutException:
            latency_ms = (time.monotonic() * 1000) - start_ms
            return ClaudeResponse(
                content="", model=self._config.model,
                input_tokens=0, output_tokens=0,
                latency_ms=latency_ms, success=False,
                error="Claude API request timed out",
            )

        except Exception as exc:
            latency_ms = (time.monotonic() * 1000) - start_ms
            return ClaudeResponse(
                content="", model=self._config.model,
                input_tokens=0, output_tokens=0,
                latency_ms=latency_ms, success=False,
                error=f"Claude API error: {type(exc).__name__}: {exc}",
            )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
