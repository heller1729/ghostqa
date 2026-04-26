"""
Utility functions for GhostQA.
"""

import json
import re
from typing import Any


def parse_json_response(text: str) -> Any:
    """
    Parse JSON from an LLM response, handling common issues:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Truncated JSON (attempts repair)
    """
    if not text or not text.strip():
        return {}

    cleaned = text.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_pattern = r"^```(?:json)?\s*\n?(.*?)```\s*$"
    match = re.match(fence_pattern, cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1).strip()

    # Try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    # Look for the first { and last }
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(cleaned[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    # Try to find JSON array
    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(cleaned[first_bracket : last_bracket + 1])
        except json.JSONDecodeError:
            pass

    # Attempt to repair truncated JSON by closing open braces/brackets
    if first_brace != -1:
        fragment = cleaned[first_brace:]
        # Count open vs close braces
        open_braces = fragment.count("{") - fragment.count("}")
        open_brackets = fragment.count("[") - fragment.count("]")

        # Remove any trailing incomplete key-value pair
        # (e.g., `"key": "unterminated...`)
        fragment = re.sub(r',?\s*"[^"]*":\s*"[^"]*$', "", fragment)
        fragment = re.sub(r',?\s*"[^"]*":\s*$', "", fragment)
        fragment = re.sub(r',\s*$', "", fragment)

        repaired = fragment + "]" * open_brackets + "}" * open_braces
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # Last resort: return empty dict
    return {}
