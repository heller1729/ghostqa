"""
GhostQA Vision Engine

Uses a Vision-Language Model to understand web page screenshots.
Supports Gemini, OpenAI, and Claude via the LLM abstraction layer.
"""

import logging
from typing import Optional, Dict, Any, List

from ghostqa.llm.base import LLMProvider, Message
from ghostqa.utils import parse_json_response


logger = logging.getLogger("ghostqa.vision")


class VisionEngine:
    """Analyzes screenshots using a Vision-Language Model."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def analyze_page(
        self,
        screenshot_base64: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Analyze a screenshot to understand the current page state.

        Returns a dict with page_type, description, interactive_elements, etc.
        """
        system_prompt = """You are a web application testing expert analyzing a screenshot.

IMPORTANT: You MUST respond with valid JSON only. No markdown, no code fences.

Analyze the screenshot carefully and identify:
1. The type of page
2. ALL visible interactive elements (buttons, links, inputs, menus, icons)
3. Any visible issues or bugs
4. What a tester should try next

Your JSON response MUST follow this exact structure:
{
    "page_type": "login|form|dashboard|list|detail|error|search|settings|other",
    "description": "A one-sentence description of what this page shows",
    "interactive_elements": [
        {"type": "button", "text": "visible text on the element", "purpose": "what clicking it does"},
        {"type": "link", "text": "link text", "purpose": "where it navigates"},
        {"type": "input", "text": "placeholder or label", "purpose": "what to type"}
    ],
    "forms": [
        {"purpose": "what the form does", "fields": ["field1", "field2"]}
    ],
    "visible_issues": ["any UI issues you can see"],
    "suggested_actions": ["click X", "fill Y", "navigate to Z"]
}

RULES:
- List EVERY interactive element you can see (buttons, links, inputs, icons, menu items)
- Be thorough — a typical web page has 10-30+ interactive elements
- Include navigation menu items, footer links, search bars, icons
- If you see a modal/dialog, list its buttons too"""

        user_prompt = "Analyze this web page screenshot and list ALL interactive elements you can see."
        if context:
            user_prompt += f"\n\nContext: {context}"

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        response = await self.provider.chat_with_image(
            messages=messages,
            image_base64=screenshot_base64,
            json_mode=True,
            max_tokens=2000,
        )

        # Debug: log raw response for troubleshooting
        logger.debug(f"Vision raw response ({len(response.content)} chars): {response.content[:200]}...")

        result = parse_json_response(response.content)

        # Validate we got something useful
        if not result or not result.get("interactive_elements"):
            logger.warning(f"Vision returned empty/incomplete analysis. Raw: {response.content[:300]}")
            # Return a minimal valid structure so the agent doesn't think the page is blank
            if not result:
                result = {}
            result.setdefault("page_type", "unknown")
            result.setdefault("description", "Page analysis incomplete — check screenshot directly")
            result.setdefault("interactive_elements", [])
            result.setdefault("visible_issues", [])
            result.setdefault("suggested_actions", ["Scroll down to see more", "Click on visible elements"])

        return result

    async def compare_states(
        self,
        before_screenshot: str,
        after_screenshot: str,
        action_taken: str,
    ) -> Dict[str, Any]:
        """Compare two screenshots to detect what changed after an action."""
        system_prompt = """You are a web application testing expert analyzing a screenshot taken AFTER a user action.
Analyze the current state and determine if it looks like expected behavior or a potential bug.

Respond in JSON format:
{
    "changed": true,
    "changes": ["change1", "change2"],
    "is_error_state": true/false,
    "error_details": "description if error detected",
    "is_expected_behavior": true/false,
    "confidence": 0.0-1.0,
    "notes": "any additional observations"
}"""

        user_prompt = f"The user performed this action: {action_taken}\n\nAnalyze the resulting page state:"

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        response = await self.provider.chat_with_image(
            messages=messages,
            image_base64=after_screenshot,
            json_mode=True,
            max_tokens=1000,
        )

        result = parse_json_response(response.content)
        return result

    async def detect_visual_bugs(
        self,
        screenshot_base64: str,
    ) -> List[Dict[str, Any]]:
        """Scan a screenshot for visual bugs and UI issues."""
        system_prompt = """You are a QA expert looking for visual bugs in web applications.
Look for:
- Overlapping or misaligned elements
- Text that's cut off or overflowing
- Missing or broken images
- Inconsistent spacing or padding
- Color contrast issues
- Elements outside viewport
- Loading spinners stuck on screen
- Empty states that shouldn't be empty
- Error messages visible to users
- Exposed stack traces or debug info

SEVERITY GUIDELINES (use the correct level, NOT always medium):
- critical: Data loss, security info exposed, app crash, authentication bypass
- high: Broken functionality, form submission fails, broken navigation, missing content
- medium: UI overlap, inconsistent styling, confusing UX, unclear labels
- low: Minor cosmetic issues, slight misalignment, minor text issues, tooltip quirks

Respond in JSON format:
{
    "issues": [
        {
            "type": "layout|text|image|contrast|overflow|error|security",
            "severity": "critical|high|medium|low",
            "description": "what the issue is",
            "location": "where on the page",
            "confidence": 0.0-1.0
        }
    ],
    "overall_quality": "good|acceptable|poor",
    "notes": "any additional observations"
}

CONFIDENCE GUIDELINES:
- 0.9-1.0: Certain bug (broken image, visible error, stack trace)
- 0.7-0.9: Likely bug (overlapping elements, low contrast)
- 0.5-0.7: Possible bug (minor alignment, subjective design choice)
- 0.0-0.5: Uncertain (might be intentional, observation, not a bug)"""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content="Scan this page for visual bugs:"),
        ]

        response = await self.provider.chat_with_image(
            messages=messages,
            image_base64=screenshot_base64,
            json_mode=True,
            max_tokens=500,
        )

        result = parse_json_response(response.content)
        return result.get("issues", [])
