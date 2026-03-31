"""Skill runner — 3-call pipeline for skill-enhanced classification.

Call 1: Skill Matching — compare event to skills.md, find top match
Call 2: Scroll Execution — run matched scroll instructions, return analysis
Call 3: Classification — enhanced with skill result (handled by classifier.py)

Skills are toggled on/off in settings. When off, only Call 3 runs.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .llm import LLMConfig, LocalLLM

logger = logging.getLogger(__name__)

SKILL_MATCH_PROMPT = """Match the event to one skill from the list. Return ONLY valid JSON with no extra text.

Return ONLY:
{"skill_id":"<id or N/A>","confidence":<0.0-1.0>}"""

SKILL_MATCH_WITH_TITLE_PROMPT = """You have two jobs. Return ONLY valid JSON with no extra text.

Job 1 — Skill Match: Compare the event to the skills list. Find the best match.
Job 2 — Event Title: Write a short 1-sentence title (max 12 words) describing the event. Also write a redacted version replacing personal names with [Name], addresses with [Address], phone numbers with [Phone], emails with [Email], registration/permit/account numbers with [Ref], pet names with [Pet]. Do NOT redact department names, council names, or document types.

Return ONLY:
{"skill_id":"<id or N/A>","confidence":<0.0-1.0>,"display_title":"<title>","display_title_redacted":"<redacted title>"}"""

SCROLL_EXECUTE_PROMPT = """Execute the scroll instructions on this event. Follow the scroll's request types, checks, and outcomes precisely.

Return ONLY one JSON block. No analysis. No explanation. No duplicate output. Stop immediately after closing the JSON.

{
  "skill_id": "<from scroll>",
  "request_type": "<type>",
  "outcome": "<outcome code>",
  "metadata": {"<field>": "<value>"},
  "analysis": "<2 sentences max>",
  "missing_info": [],
  "response_template_key": "<template key>",
  "suggested_folder": "<department_key>",
  "confidence": <0.0-1.0>
}"""


def load_skills_list(skills_dir: Path) -> str | None:
    """Load the master skills list from skills.md."""
    skills_path = skills_dir / "skills.md"
    if skills_path.is_file():
        return skills_path.read_text(encoding="utf-8")
    return None


def load_scroll(skills_dir: Path, skill_id: str) -> dict[str, Any] | None:
    """Load a scroll JSON file for a skill."""
    scroll_path = skills_dir / f"{skill_id}_scroll.json"
    if scroll_path.is_file():
        try:
            return json.loads(scroll_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load scroll %s: %s", scroll_path, exc)
    return None


def call1_match_skill(
    llm: LocalLLM,
    skills_list: str,
    event_text: str,
    generate_title: bool = False,
) -> dict[str, Any]:
    """Call 1: Match event against skills list, optionally generate display title.

    Args:
        llm: Local LLM instance.
        skills_list: Contents of skills.md.
        event_text: Formatted event text.
        generate_title: If True, use the prompt that also generates display titles.

    Returns:
        Dict with skill_id, confidence, and optionally display_title/display_title_redacted.
    """
    user_message = f"## SKILLS LIST:\n{skills_list}\n\n## EVENT:\n{event_text}"

    if generate_title:
        prompt = SKILL_MATCH_WITH_TITLE_PROMPT
        max_tokens = 128
    else:
        prompt = SKILL_MATCH_PROMPT
        max_tokens = 64

    response = llm.infer(prompt, user_message, use_json_schema=False, max_tokens_override=max_tokens)

    if not response.success:
        return {"skill_id": "none", "confidence": 0.0, "reasoning": f"Match failed: {response.error}"}

    content = response.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

    try:
        result = json.loads(content)
        result.setdefault("reasoning", "")
        result["llm_tokens"] = response.tokens_used
        result["llm_latency_ms"] = response.latency_ms

        skill_id = result.get("skill_id", "N/A")
        if skill_id == "N/A" or skill_id == "none":
            result["skill_id"] = "none"

        logger.info(
            "Skill match: %s (%.2f)",
            result.get("skill_id"), result.get("confidence", 0),
        )
        return result
    except json.JSONDecodeError:
        return {"skill_id": "none", "confidence": 0.0, "reasoning": "Failed to parse match response"}


def call2_execute_scroll(
    llm: LocalLLM,
    scroll: dict[str, Any],
    event_text: str,
) -> dict[str, Any]:
    """Call 2: Execute a scroll's instructions on the event.

    Returns structured analysis with request type, outcome, metadata.
    """
    scroll_json = json.dumps(scroll, indent=2)
    user_message = f"## SCROLL:\n```json\n{scroll_json}\n```\n\n## EVENT:\n{event_text}"

    response = llm.infer(SCROLL_EXECUTE_PROMPT, user_message, use_json_schema=False)

    if not response.success:
        return {
            "skill_id": scroll.get("skill_id", "unknown"),
            "request_type": "error",
            "outcome": "execution_failed",
            "metadata": {},
            "analysis": f"Scroll execution failed: {response.error}",
            "missing_info": [],
            "response_template_key": "",
            "suggested_folder": scroll.get("department_key", ""),
            "confidence": 0.0,
            "error": response.error,
        }

    content = response.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```[\s\S]*$", "", content)

    # Extract first complete JSON object only
    brace_count = 0
    end_idx = 0
    for i, ch in enumerate(content):
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break
    if end_idx > 0:
        content = content[:end_idx]

    try:
        result = json.loads(content)
        result["llm_tokens"] = response.tokens_used
        result["llm_latency_ms"] = response.latency_ms
        logger.info(
            "Scroll executed: %s → %s/%s (%.2f)",
            result.get("skill_id"), result.get("request_type"), result.get("outcome"), result.get("confidence", 0),
        )
        return result
    except json.JSONDecodeError:
        return {
            "skill_id": scroll.get("skill_id", "unknown"),
            "request_type": "error",
            "outcome": "parse_failed",
            "metadata": {},
            "analysis": "Failed to parse scroll execution response",
            "missing_info": [],
            "response_template_key": "",
            "suggested_folder": scroll.get("department_key", ""),
            "confidence": 0.0,
            "raw_response": content,
        }


def run_skill_pipeline(
    llm: LocalLLM,
    skills_dir: Path,
    event_text: str,
    confidence_threshold: float = 0.80,
) -> dict[str, Any] | None:
    """Run the full skill pipeline (Call 1 + Call 2).

    Returns the skill result dict if a skill matched and executed,
    or None if no skill matched above threshold.
    """
    # Load skills list
    skills_list = load_skills_list(skills_dir)
    if not skills_list:
        logger.info("No skills.md found, skipping skill pipeline")
        return None

    # Call 1: Match
    match_result = call1_match_skill(llm, skills_list, event_text)
    skill_id = match_result.get("skill_id", "none")
    confidence = match_result.get("confidence", 0.0)

    if skill_id == "none" or confidence < confidence_threshold:
        logger.info("No skill match above threshold (%.2f < %.2f)", confidence, confidence_threshold)
        return None

    # Load scroll
    scroll = load_scroll(skills_dir, skill_id)
    if not scroll:
        logger.warning("Skill '%s' matched but scroll file not found", skill_id)
        return None

    # Call 2: Execute
    skill_result = call2_execute_scroll(llm, scroll, event_text)
    skill_result["match_confidence"] = confidence
    skill_result["match_reasoning"] = match_result.get("reasoning", "")

    return skill_result


def save_skill_result(event_dir: Path, skill_result: dict[str, Any]) -> None:
    """Save skill analysis result to the event folder."""
    result_path = event_dir / "_skill_result.json"
    result_path.write_text(json.dumps(skill_result, indent=2))
    logger.info("Skill result saved: %s", result_path)


def load_skill_result(event_dir: Path) -> dict[str, Any] | None:
    """Load existing skill result from an event folder."""
    result_path = event_dir / "_skill_result.json"
    if result_path.is_file():
        try:
            return json.loads(result_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


def get_response_template(
    skills_dir: Path,
    skill_id: str,
    template_key: str,
) -> str | None:
    """Get a response template from a scroll by key."""
    scroll = load_scroll(skills_dir, skill_id)
    if scroll:
        return scroll.get("response_templates", {}).get(template_key)
    return None
