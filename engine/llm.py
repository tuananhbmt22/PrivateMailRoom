"""Local LLM client for the Mailroom classification engine.

Communicates with an OpenAI-compatible API endpoint on the local network.
Zero outbound internet calls — endpoint must be a local/LAN address.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for the local LLM endpoint."""

    endpoint: str
    model: str
    api_path: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    response_format: str

    @classmethod
    def from_yaml(cls, config_path: Path) -> LLMConfig:
        """Load LLM configuration from a YAML file."""
        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)
        return cls(
            endpoint=raw["endpoint"],
            model=raw["model"],
            api_path=raw.get("api_path", "/v1/chat/completions"),
            temperature=raw.get("temperature", 0.0),
            max_tokens=raw.get("max_tokens", 1024),
            timeout_seconds=raw.get("timeout_seconds", 120),
            response_format=raw.get("response_format", "json"),
        )

    @property
    def url(self) -> str:
        """Full URL for the chat completions endpoint."""
        return f"{self.endpoint.rstrip('/')}{self.api_path}"


@dataclass
class LLMResponse:
    """Response from the local LLM."""

    content: str
    model: str
    tokens_used: int
    latency_ms: float
    success: bool
    error: str | None = None


class LocalLLM:
    """Client for a local OpenAI-compatible LLM API."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = httpx.Client(timeout=config.timeout_seconds)

    def infer(self, system_prompt: str, user_message: str, use_json_schema: bool = True, max_tokens_override: int | None = None) -> LLMResponse:
        """Send a request to the local LLM.

        Args:
            system_prompt: The system prompt for this call.
            user_message: The user message content.
            use_json_schema: If True, enforce the classification JSON schema.
                             If False, let the LLM return freeform JSON (for skills).

        Returns:
            LLMResponse with the raw content string and metadata.
        """
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": self._config.temperature,
            "max_tokens": max_tokens_override or self._config.max_tokens,
        }

        if self._config.response_format == "json" and use_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "classification_result_v2",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "event_id": {"type": "string"},
                            "file_count": {"type": "integer"},
                            "outcome": {"type": "string"},
                            "sub_item_id": {"type": ["string", "null"]},
                            "sub_item_name": {"type": ["string", "null"]},
                            "confidence": {"type": "number"},
                            "sub_item_confidence": {"type": "number"},
                            "reasoning": {"type": "string"},
                            "display_title": {"type": "string"},
                            "display_title_redacted": {"type": "string"},
                            "linked_files": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "event_id",
                            "file_count",
                            "outcome",
                            "sub_item_id",
                            "sub_item_name",
                            "confidence",
                            "sub_item_confidence",
                            "reasoning",
                            "display_title",
                            "display_title_redacted",
                            "linked_files",
                        ],
                        "additionalProperties": False,
                    },
                },
            }

        start_ms = time.monotonic() * 1000

        try:
            response = self._client.post(self._config.url, json=payload)
            latency_ms = (time.monotonic() * 1000) - start_ms

            if response.status_code != 200:
                error_msg = f"LLM returned HTTP {response.status_code}: {response.text[:500]}"
                logger.error(error_msg)
                return LLMResponse(
                    content="",
                    model=self._config.model,
                    tokens_used=0,
                    latency_ms=latency_ms,
                    success=False,
                    error=error_msg,
                )

            body = response.json()
            message = body["choices"][0]["message"]
            content = message.get("content") or ""

            # Some models (e.g., Qwen 9B) put the response in reasoning_content
            if not content.strip() and message.get("reasoning_content"):
                reasoning = message["reasoning_content"]
                # Try to find JSON in the reasoning content
                # First check if there's a complete JSON object
                import re as _re
                json_match = _re.search(r'\{[^{}]*"outcome"[^{}]*\}', reasoning)
                if json_match:
                    content = json_match.group(0)
                else:
                    # Use the full reasoning as content — the parser will try to extract JSON
                    content = reasoning

            # Also check: content exists but reasoning_content has the actual JSON
            if content.strip() and message.get("reasoning_content"):
                reasoning = message["reasoning_content"]
                # If content has no JSON but reasoning does, prefer reasoning
                if '{' not in content and '{' in reasoning:
                    content = reasoning

            tokens_used = body.get("usage", {}).get("total_tokens", 0)

            # Log truncation warning
            finish_reason = body["choices"][0].get("finish_reason", "")
            if finish_reason == "length":
                logger.warning("LLM response truncated (hit token limit) — may be incomplete")

            logger.info(
                "LLM inference completed in %.0fms, %d tokens (finish: %s)",
                latency_ms,
                tokens_used,
                finish_reason,
            )

            return LLMResponse(
                content=content,
                model=body.get("model", self._config.model),
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                success=True,
            )

        except httpx.TimeoutException:
            latency_ms = (time.monotonic() * 1000) - start_ms
            error_msg = f"LLM inference timed out after {self._config.timeout_seconds}s"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self._config.model,
                tokens_used=0,
                latency_ms=latency_ms,
                success=False,
                error=error_msg,
            )

        except httpx.ConnectError as exc:
            latency_ms = (time.monotonic() * 1000) - start_ms
            error_msg = f"Cannot connect to LLM at {self._config.url}: {exc}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self._config.model,
                tokens_used=0,
                latency_ms=latency_ms,
                success=False,
                error=error_msg,
            )

        except Exception as exc:
            latency_ms = (time.monotonic() * 1000) - start_ms
            error_msg = f"LLM inference failed: {type(exc).__name__}: {exc}"
            logger.error(error_msg)
            return LLMResponse(
                content="",
                model=self._config.model,
                tokens_used=0,
                latency_ms=latency_ms,
                success=False,
                error=error_msg,
            )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()
