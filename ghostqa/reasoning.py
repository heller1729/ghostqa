"""
GhostQA Reasoning Engine

LLM-powered decision making for exploration and testing strategies.
Supports Gemini, OpenAI, and Claude via the LLM abstraction layer.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel
import json

from ghostqa.llm.base import LLMProvider, Message
from ghostqa.utils import parse_json_response


VALID_ACTIONS = ("click", "fill", "navigate", "scroll", "press", "wait", "done", "fill_form")


class Action(BaseModel):
    """Represents an action the agent can take."""
    action_type: str  # click, fill, navigate, scroll, press, wait, done, fill_form
    target: Optional[str] = None  # selector, text, or URL
    value: Optional[str] = None  # for fill actions
    reasoning: Optional[str] = None  # why this action was chosen
    fields: Optional[List[Dict[str, str]]] = None  # For fill_form: [{"target": "...", "value": "..."}]
    thinking: Optional[str] = None  # chain-of-thought reasoning (debug)


class ReasoningEngine:
    """Makes decisions about what actions to take during exploration."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def decide_next_action(
        self,
        page_analysis: Dict[str, Any],
        history: List[Dict[str, Any]],
        screenshot_base64: Optional[str] = None,
        explored_elements: List[str] = None,
        failed_targets: List[str] = None,
        goal: Optional[str] = None,
        phase: str = "explore",
        discovered_forms: List[Dict[str, Any]] = None,
        context_summary: str = "",
    ) -> Action:
        """
        Decide the next action based on current page state, screenshot, and history.
        
        Args:
            phase: 'explore' for human-like UI exploration, 'test' for security testing
            discovered_forms: Forms found during exploration phase (used in test phase)
            context_summary: Summary of previous scan findings
        """
        if phase == "explore":
            system_prompt = self._get_explore_prompt()
        else:
            system_prompt = self._get_test_prompt(discovered_forms or [])

        # Build context
        history_summary = self._summarize_history(history)
        explored_summary = ""
        if explored_elements:
            explored_summary = f"\n\nElements already interacted with (DO NOT click these again):\n- " + "\n- ".join(explored_elements[-15:])

        failed_summary = ""
        if failed_targets:
            failed_summary = f"\n\nFAILED click targets (DO NOT try these, they don't work):\n- " + "\n- ".join(list(set(failed_targets))[-10:])

        # Render DOM elements as clickable targets
        dom_summary = self._render_dom_elements(page_analysis)

        user_prompt = f"""Current page analysis (from vision model):
{json.dumps({k: v for k, v in page_analysis.items() if k not in ('dom_links', 'dom_buttons', 'dom_forms')}, indent=2)}

Clickable elements from DOM:
{dom_summary}

Actions taken so far:
{history_summary}{explored_summary}{failed_summary}

Look at the attached screenshot of the page. First THINK about what a human would do, then decide your action."""

        # Inject previous scan context if available
        if context_summary:
            user_prompt += f"\n\n{context_summary}"

        # Inject USER-PROVIDED FOCUS CONTEXT (highest priority)
        if goal:
            user_prompt += f"""

🎯 USER FOCUS INSTRUCTIONS (HIGHEST PRIORITY — follow these closely):
{goal}

RULES FOR USER INSTRUCTIONS:
- These instructions override default exploration behavior.
- If the user says to navigate to a specific page, go there FIRST.
- If the user specifies a testing focus (security, UI, edge cases), prioritize that.
- After 3-5 steps, evaluate: are these instructions achievable on this app?
  If not (e.g., the page doesn't exist), note it in your "thinking" and switch to general exploration.
- Always follow the user's intent, even if it conflicts with the coverage checklist."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        # Use vision if screenshot is available, otherwise text-only
        if screenshot_base64:
            response = await self.provider.chat_with_image(
                messages=messages,
                image_base64=screenshot_base64,
                json_mode=True,
                max_tokens=700,
            )
        else:
            response = await self.provider.chat(
                messages=messages,
                json_mode=True,
                max_tokens=700,
            )

        result = parse_json_response(response.content)

        # Extract thinking (logged by agent for debugging)
        thinking = result.get("thinking", "")

        # Handle missing or malformed response
        action_type = result.get("action_type", "click")
        if action_type not in VALID_ACTIONS:
            action_type = "click"

        # Override "wait" — prefer scrolling or clicking over idling
        if action_type == "wait":
            action_type = "scroll"
            result["target"] = "down"
            result["reasoning"] = "Overridden wait → scroll to discover more content"

        # Ensure scroll always has a direction
        if action_type == "scroll" and not result.get("target"):
            result["target"] = "down"

        # Limit consecutive scrolls: if last 3 actions were all scrolls, try clicking instead
        if action_type == "scroll" and len(history) >= 3:
            recent_actions = [h.get("action", {}).get("action_type") for h in history[-3:]]
            if all(a == "scroll" for a in recent_actions):
                # Try to click something from the page analysis suggestions
                suggestions = page_analysis.get("suggested_actions", [])
                elements = page_analysis.get("interactive_elements", [])
                if elements:
                    action_type = "click"
                    result["target"] = elements[0].get("text", elements[0].get("purpose", "Account"))
                    result["reasoning"] = "Too many scrolls — clicking an unexplored element instead"
                elif suggestions:
                    action_type = "navigate"
                    result["target"] = "/#/login"
                    result["reasoning"] = "Too many scrolls — navigating to login page instead"

        return Action(
            action_type=action_type,
            target=result.get("target"),
            value=result.get("value"),
            reasoning=result.get("reasoning", "Exploring the application"),
            thinking=thinking,
            fields=result.get("fields"),
        )

    async def decide_and_analyze(
        self,
        screenshot_base64: str,
        dom_elements: Dict[str, Any],
        history: List[Dict[str, Any]],
        explored_elements: List[str] = None,
        failed_targets: List[str] = None,
        goal: Optional[str] = None,
        phase: str = "explore",
        discovered_forms: List[Dict[str, Any]] = None,
        context_summary: str = "",
        page_context: Dict[str, Any] = None,
    ) -> tuple:
        """
        TURBO MODE: Single unified LLM call that sees the screenshot,
        analyzes the page, detects bugs, and decides the next action.
        
        Args:
            page_context: Rich DOM snapshot from browser.get_page_context()
        
        Returns:
            (Action, List[dict]) — the next action and any bugs found
        """
        history_summary = self._summarize_history(history)
        explored_summary = ""
        if explored_elements:
            explored_summary = f"\n\nElements already interacted with (DO NOT click these again):\n- " + "\n- ".join(explored_elements[-15:])
        
        failed_summary = ""
        if failed_targets:
            failed_summary = f"\n\nFAILED click targets (DO NOT try these):\n- " + "\n- ".join(list(set(failed_targets))[-10:])
        
        # Build DOM summary
        dom_summary = self._render_dom_elements(dom_elements)
        
        # Build rich page context section
        page_context_section = self._render_page_context(page_context) if page_context else ""
        
        # Build last-3-actions context so LLM never loses track
        recent_actions = ""
        if history and len(history) > 0:
            last_n = history[-3:]
            recent_lines = []
            for h in last_n:
                a = h.get('action', {})
                status = '✅' if h.get('success') else '❌'
                recent_lines.append(f"  {status} Step {h.get('step', '?')}: {a.get('action_type', '?')} → {a.get('target', '?')} | {a.get('reasoning', '')[:80]}")
            recent_actions = "\n".join(recent_lines)
        
        # Count recent scrolls to detect scroll-loops
        recent_scroll_count = 0
        if history:
            for h in history[-4:]:
                if h.get('action', {}).get('action_type') == 'scroll':
                    recent_scroll_count += 1
        
        scroll_warning = ""
        if recent_scroll_count >= 2:
            scroll_warning = "\n⚠️ WARNING: You have scrolled multiple times recently. You MUST now click a link, button, or fill a form. Do NOT scroll again."

        system_prompt = f"""You are GhostQA, an autonomous web testing agent. You can SEE the page screenshot directly.

Your PRIMARY job is to FIND BUGS. Your secondary job is to navigate to new pages to find more bugs.

{"🎯 USER FOCUS (HIGHEST PRIORITY): " + goal if goal else ""}

=== YOUR LAST ACTIONS (do NOT repeat these) ===
{recent_actions if recent_actions else "(first step)"}
{scroll_warning}

=== STRICT RULES ===
1. EVERY step MUST output a MEANINGFUL ACTION (click, fill, navigate, press). Scroll is only acceptable on a brand-new page you haven't scrolled yet.
2. NEVER repeat the same action as your last step. If you just scrolled, you MUST now click or navigate.
3. PRIORITIZE actions in this order: fill form fields > click buttons/links > navigate to new pages > scroll (LAST RESORT).
4. If you see forms on the page, FILL THEM with test data before doing anything else.
5. If you've already scrolled this page, pick a link or button from the DOM elements list and CLICK it.
6. If nothing interesting is left on this page, NAVIGATE to a new unvisited page.

EVERY STEP — do this in order:
1. SCAN the screenshot for visual bugs
2. LIST every bug you find in bugs_found
3. Pick a NEW action you haven't done before (click a new element, fill a form, navigate to a new page)

BUG DETECTION CHECKLIST:
□ PRICES: $0, negative, decimal errors, math errors, suspicious values
□ LAYOUT: Overlapping, misaligned, broken grid, inconsistent spacing
□ IMAGES: Broken, missing, stretched, wrong aspect ratio, placeholder
□ TEXT: Typos, truncated, overflow, lorem ipsum, mixed fonts, wrong language
□ COLORS: Low contrast, inconsistent theme colors
□ FORMS: Missing labels, wrong field types, placeholder-as-label, no validation
□ SECURITY: Autocomplete on passwords, credentials exposed, weak CAPTCHA
□ ACCESSIBILITY: Missing alt text, missing ARIA labels, no landmarks, no h1

SEVERITY: critical (data loss/security) > high (broken features) > medium (visual) > low (cosmetic)

MODAL/OVERLAY: If a modal blocks content, dismiss it first (click X/Close/Accept or press Escape)

Respond in JSON:
{{
    "thinking": "What I see, bugs I notice, what NEW action to take next",
    "bugs_found": [
        {{"description": "specific bug", "severity": "critical|high|medium|low", "type": "visual|layout|text|error|price|image|security|accessibility|functional"}}
    ],
    "action_type": "click|fill|fill_form|navigate|scroll|press|done",
    "target": "element text or CSS selector",
    "value": "for fill actions",
    "fields": [{{"target": "selector", "value": "data"}}],
    "reasoning": "why this specific new action"
}}

CRITICAL: You MUST choose a DIFFERENT action than your last step. If your last action was scroll, you MUST click, fill, or navigate now."""

        user_prompt = f"""PAGE CONTEXT (from DOM analysis):
{page_context_section}

Clickable elements from DOM:
{dom_summary}

Actions taken so far:
{history_summary}{explored_summary}{failed_summary}

LOOK CAREFULLY at the screenshot. Cross-reference what you SEE with the DOM context above. Report ALL visual AND structural bugs, then decide your next action."""

        if context_summary:
            user_prompt += f"\n\n{context_summary}"

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        response = await self.provider.chat_with_image(
            messages=messages,
            image_base64=screenshot_base64,
            json_mode=True,
            max_tokens=1500,
        )

        result = parse_json_response(response.content)
        thinking = result.get("thinking", "")
        
        # Extract bugs
        bugs = result.get("bugs_found", [])
        
        # Extract action
        action_type = result.get("action_type", "click")
        if action_type not in VALID_ACTIONS:
            action_type = "click"
        
        if action_type == "wait":
            action_type = "scroll"
            result["target"] = "down"
        
        if action_type == "scroll" and not result.get("target"):
            result["target"] = "down"
        
        # Prevent LLM from repeating the exact same action
        if history and len(history) > 0:
            last = history[-1].get("action", {})
            last_type = last.get("action_type")
            last_target = last.get("target")
            if (action_type == last_type and 
                result.get("target") == last_target and
                action_type != "scroll"):
                # Same exact action — force a different one
                action_type = "scroll"
                result["target"] = "down"
                result["reasoning"] = "Forced scroll to break repeat loop — will pick new action next step"
        
        action = Action(
            action_type=action_type,
            target=result.get("target"),
            value=result.get("value"),
            reasoning=result.get("reasoning", "Exploring the application"),
            thinking=thinking,
            fields=result.get("fields"),
        )
        
        return action, bugs

    def _get_explore_prompt(self) -> str:
        """System prompt for Phase 1: human-like UI exploration."""
        return """You are a curious human user visiting a web application for the first time.
Your goal is to explore EVERY page, feature, and interactive element — like a real user who
wants to understand the entire app before making a purchase or signing up.

BEFORE choosing an action, you MUST think step by step:
1. What kind of page am I looking at? (e-commerce, login, settings, etc.)
2. Have I SCROLLED DOWN on this page to see everything below the fold?
3. What would a normal human do next? (browse products, click items, open menus)
4. Which flows from the COVERAGE CHECKLIST have I NOT done yet?
5. What interactive elements on screen haven't I tried?

SCROLLING RULE (CRITICAL):
- When you land on a NEW page, you MUST scroll down ONCE before doing anything else.
- This ensures you see below-the-fold content like footers, additional products, and hidden links.
- After scrolling, continue exploring the page's interactive elements.

COVERAGE CHECKLIST — mentally track which you've done:
□ Home/product listing — browse products, scroll to see all items
□ Product detail — click on at least ONE product card to open its detail page
□ Search — use the search bar to search for something
□ Add to cart — add a product to the shopping cart
□ Cart/basket — visit the cart and see what's inside
□ Login page — visit the login form (try "Forgot password?" link too)
□ Registration — visit the registration form and fill it out
□ Contact/feedback — visit the contact or feedback page
□ About/info — visit about, terms, legal, or info pages
□ User profile/settings — if logged in, visit profile, orders, account settings
□ Any sidebar menus, dropdown menus, or hamburger menus — open and explore

RULES FOR EXPLORATION:
- Explore like a CURIOUS USER, not a robot. Click things that look interesting.
- Visit ALL main navigation links before diving deep into any one page.
- When you see a form, fill it with REALISTIC data (real-looking name, email, address) — NOT attack payloads.
- CLICK on product cards, images, and items — real users browse product details.
- After logging in or registering, explore authenticated pages (profile, orders, basket).
- Try the "Forgot Password?" link if you see it — it's a real user flow.
- NEVER repeat an action you already took — check the action history.
- If you already explored an element (listed in explored_elements), skip it.
- Use CSS selectors from dom_buttons/dom_links when text labels are empty or unclear.
- Do NOT try security attacks (SQL injection, XSS, etc.) during this phase.

MODAL/OVERLAY HANDLING (CRITICAL — do this FIRST):
- If a modal, popup, cookie banner, or overlay is blocking the main content, DISMISS IT IMMEDIATELY.
- Click the X button, Close button, Accept button, or press Escape to close it.
- Look for small/hidden close buttons — they may be disguised (e.g., "©lose" or tiny X icons).
- Do NOT just observe the modal — TAKE ACTION to close it. Observing is not enough.
- If a close button is hard to find, try clicking OUTSIDE the modal, pressing Escape, or using CSS selectors.
- Spend AT MOST 2 actions trying to dismiss a single overlay. If it won't close, move on.

SELF-AWARENESS — compare to previous steps:
- Look at your action history. If the SAME elements appear across multiple steps without change,
  you are STUCK. Try a completely different approach (press Escape, click elsewhere, navigate away).
- If the page looks identical to the last 2+ steps, the visual context has NOT changed — act differently.

ANTI-REPETITION:
- Before choosing an action, check the "Recent actions" list. If you see you already
  clicked/filled the same target, pick something DIFFERENT.
- If you've been on the same page for 3+ actions, NAVIGATE to a different page.

Respond in JSON format:
{
    "thinking": "Step-by-step: what page am I on, did I scroll, what's unchecked on my coverage list, what should I do next",
    "action_type": "click|fill|navigate|scroll|press|done",
    "target": "the text content or CSS selector of the element",
    "value": "value for fill actions, null otherwise",
    "reasoning": "brief explanation of why this action makes sense for a user"
}

Action types:
- click: Click an element (target = visible text or CSS selector like '#id' or '.class')
- fill: Type into a SINGLE form field (target = field label or selector, value = realistic test data)
- fill_form: Fill MULTIPLE fields AND submit in one step (use for login/register/search forms):
  Set target = the submit button text or selector, and add a "fields" array:
  {"action_type": "fill_form", "target": "SIGN IN", "fields": [{"target": "#email", "value": "test@test.com"}, {"target": "#password", "value": "pass123"}]}
- navigate: Go to a URL (target = full or relative URL)
- scroll: Scroll the page (target = "down" or "up")
- press: Press a keyboard key (target = "Enter", "Tab", "Escape")
- done: All exploration is complete (use ONLY when you've covered most of the checklist)

SPEED RULE: When you see a form with 2+ fields, ALWAYS use fill_form instead of individual fill actions.

IMPORTANT: The "thinking" field must show your reasoning process. Do NOT skip it."""

    def _get_test_prompt(self, discovered_forms: List[Dict[str, Any]]) -> str:
        """System prompt for Phase 2: targeted security and edge-case testing."""
        forms_summary = ""
        if discovered_forms:
            forms_summary = "\nForms discovered during exploration:\n"
            for form in discovered_forms:
                forms_summary += f"  - URL: {form.get('url', '?')} | Fields: {', '.join(form.get('fields', []))}\n"
        
        return f"""You are a security tester. You have already explored this web application and now
you must systematically test it for vulnerabilities, edge cases, and broken behavior.
{forms_summary}
BEFORE choosing an action, you MUST think step by step:
1. Which forms have I already tested? Which are UNTESTED?
2. What attack did I just try? Did it reveal a vulnerability?
3. Should I move to a DIFFERENT form, or try another vector on this one?
4. Am I repeating myself? If yes, move on.

TESTING STRATEGIES (try these on every form):
1. Authentication bypass: Try SQL injection on login — ' OR 1=1 -- in email, any password
2. XSS: Enter <script>alert('xss')</script> in text/comment/name fields
3. SQL Injection: ' OR 1=1 -- in email, search, and text fields
4. Default credentials: admin@admin.com / admin, admin@juice-sh.op / admin123
5. Empty required fields: Submit with mandatory fields blank
6. Wrong format: Letters in number fields, numbers in email fields
7. Very long input: 500+ characters in short text fields
8. Boundary values: 0, -1, 99999 in numeric/quantity fields
9. Special characters: < > & ' " / \\ in text fields

ANTI-DUPLICATION (CRITICAL):
- Do NOT report the same bug with slightly different wording. ONE report per unique issue.
- Check the action history — if you already attacked a field with the same payload, skip it.
- After 2 attack attempts on the SAME form field, MOVE to a different form or page.
- Do NOT spend more than 3 steps on any single page.

MOVE-ON RULE:
- If you've tested login → move to register → then contact → then search
- Cover ALL forms, not just the first one you see.
- Navigate to untested pages. Use the forms list above to track progress.

RULES:
- Focus on FORMS and INPUT FIELDS — that's where vulnerabilities hide.
- After each attack, check if the page shows an error, accepts it, or crashes.
- Navigate to pages with forms you haven't tested yet.
- Use CSS selectors when text labels are unclear.
- Try to LOG IN using exploitation — this is a high-value target.

Respond in JSON format:
{{
    "thinking": "Step-by-step: what have I tested, what's untested, what attack to try next",
    "action_type": "click|fill|navigate|scroll|press|done",
    "target": "the text content or CSS selector of the element",
    "value": "attack payload or test value for fill actions, null otherwise",
    "reasoning": "what vulnerability or edge case this tests"
}}

Action types:
- click: Click an element (target = visible text or CSS selector)
- fill: Type into a form field (target = field label or selector, value = attack payload)
- navigate: Go to a URL to test a different form
- scroll: Scroll the page
- press: Press a keyboard key (target = "Enter", "Tab", "Escape")
- done: All security tests are complete

IMPORTANT: The "thinking" field must show your reasoning process. Do NOT skip it."""

    async def generate_test_inputs(
        self,
        field_info: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate test inputs for a form field."""
        system_prompt = """You are generating test inputs for a form field.
Generate diverse inputs to test:
1. Valid typical input
2. Empty/blank input
3. Minimum boundary
4. Maximum boundary (very long input)
5. Special characters
6. SQL injection attempt
7. XSS attempt
8. Invalid format (if applicable)

Respond in JSON format:
{
    "test_cases": [
        {
            "input": "the test value",
            "category": "valid|empty|boundary|special|injection|xss|invalid",
            "should_succeed": true/false,
            "description": "what this tests"
        }
    ]
}"""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=f"Generate test inputs for this field:\n{json.dumps(field_info, indent=2)}"),
        ]

        response = await self.provider.chat(
            messages=messages,
            json_mode=True,
            max_tokens=800,
        )

        result = parse_json_response(response.content)
        return result.get("test_cases", [])

    async def evaluate_result(
        self,
        action: Action,
        before_state: Dict[str, Any],
        after_state: Dict[str, Any],
        console_errors: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate the result of an action to determine if it revealed a bug."""
        system_prompt = """You are evaluating the result of a test action.
Determine if the result indicates a bug or unexpected behavior.

Consider:
- Did the action produce the expected result?
- Are there any console errors?
- Is the new state valid?
- Are there signs of the application mishandling the input?

Respond in JSON format:
{
    "is_bug": true/false,
    "bug_type": "functional|visual|validation|security|performance|null",
    "severity": "critical|high|medium|low|null",
    "description": "description of the issue if bug found",
    "confidence": 0.0-1.0,
    "false_positive_risk": "low|medium|high"
}"""

        user_prompt = f"""Action taken: {action.action_type} on "{action.target}"
{f'Value: {action.value}' if action.value else ''}

Before state:
{json.dumps(before_state, indent=2)}

After state:
{json.dumps(after_state, indent=2)}

Console errors during action:
{json.dumps(console_errors, indent=2) if console_errors else 'None'}

Was this a bug or expected behavior?"""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        response = await self.provider.chat(
            messages=messages,
            json_mode=True,
            max_tokens=500,
        )

        return parse_json_response(response.content)

    def _summarize_history(self, history: List[Dict[str, Any]], max_items: int = 8) -> str:
        """Summarize recent history for context."""
        if not history:
            return "No previous actions yet. Start exploring!"

        recent = history[-max_items:]
        lines = []
        for i, item in enumerate(recent):
            action = item.get("action", {})
            result = item.get("result", "unknown")
            url = item.get("url", "")
            lines.append(
                f"  {i+1}. [{result}] {action.get('action_type', '?')} "
                f"→ {action.get('target', '?')} (on {url})"
            )

        return "\n".join(lines)

    @staticmethod
    def _render_dom_elements(page_analysis: Dict[str, Any]) -> str:
        """Render DOM elements as a readable list for the LLM prompt."""
        lines = []

        # Buttons
        buttons = page_analysis.get("dom_buttons", [])
        if buttons:
            lines.append("BUTTONS:")
            for btn in buttons[:12]:
                text = btn.get("text", "").strip()
                selector = btn.get("selector", "")
                label = text or f"(icon-only, use selector: {selector})"
                lines.append(f"  - {label}  [selector: {selector}]")

        # Links
        links = page_analysis.get("dom_links", [])
        if links:
            lines.append("LINKS:")
            for link in links[:15]:
                text = link.get("text", "").strip()
                href = link.get("href", "")
                if text:
                    lines.append(f"  - [{text}] -> {href}")

        # Forms
        forms = page_analysis.get("dom_forms", [])
        if forms:
            lines.append("FORMS:")
            for form in forms[:5]:
                inputs = form.get("inputs", [])
                input_names = [inp.get("name") or inp.get("id") or inp.get("type", "?") for inp in inputs[:5]]
                lines.append(f"  - Form ({form.get('method', 'GET')} {form.get('action', '')}): fields = {input_names}")

        return "\n".join(lines) if lines else "No interactive DOM elements found."

    @staticmethod
    def _render_page_context(page_context: Dict[str, Any]) -> str:
        """Render rich page context as a readable block for the LLM prompt."""
        if not page_context:
            return "No page context available."
        
        lines = []
        
        # Page title
        title = page_context.get("title", "")
        if title:
            lines.append(f"Page Title: {title}")
        
        # Meta description
        meta = page_context.get("meta_description", "")
        if meta:
            lines.append(f"Meta Description: {meta[:120]}")
        
        # Headings structure
        headings = page_context.get("headings", [])
        if headings:
            lines.append("Page Structure (headings):")
            for h in headings[:10]:
                indent = "  " * (int(h["level"][1]) - 1) if h.get("level") else "  "
                lines.append(f"{indent}{h.get('level', '?')}: {h.get('text', '')}")
        
        # Images summary
        images = page_context.get("images", [])
        if images:
            missing_alt = sum(1 for img in images if img.get("alt") == "**MISSING**")
            broken = sum(1 for img in images if img.get("broken"))
            lines.append(f"Images: {len(images)} visible ({missing_alt} missing alt, {broken} broken)")
            # Show first few problematic images
            for img in images[:8]:
                status = ""
                if img.get("alt") == "**MISSING**":
                    status = " ⚠️ NO ALT"
                elif img.get("broken"):
                    status = " ❌ BROKEN"
                lines.append(f"  - {img.get('src', '?')} ({img.get('width', '?')}x{img.get('height', '?')}, alt=\"{img.get('alt', '')}\"){status}")
        
        # Accessibility issues (pre-computed by browser.get_page_context)
        a11y = page_context.get("accessibility_issues", [])
        if a11y:
            lines.append("ACCESSIBILITY ISSUES DETECTED (report these as bugs):")
            for issue in a11y:
                lines.append(f"  ⚠️ {issue}")
        
        # Layout issues
        layout = page_context.get("layout_issues", [])
        if layout:
            lines.append("LAYOUT ISSUES DETECTED (report these as bugs):")
            for issue in layout:
                lines.append(f"  📐 {issue}")
        
        # Broken/dead links
        broken_links = page_context.get("broken_links", [])
        if broken_links:
            lines.append(f"BROKEN/DEAD LINKS ({len(broken_links)} found, report as bugs):")
            for link in broken_links:
                lines.append(f"  🔗 {link}")
        
        # Tiny text
        tiny = page_context.get("tiny_text", [])
        if tiny:
            lines.append("TINY TEXT DETECTED (report as accessibility/visual bugs):")
            for t in tiny:
                lines.append(f"  🔍 {t}")
        
        # Security issues
        security = page_context.get("security_issues", [])
        if security:
            lines.append("SECURITY ISSUES DETECTED (report these as bugs):")
            for issue in security:
                lines.append(f"  🔒 {issue}")
        
        # Duplicate IDs
        dup_ids = page_context.get("duplicate_ids", [])
        if dup_ids:
            lines.append("DUPLICATE IDs DETECTED (report as functional bugs):")
            for dup in dup_ids:
                lines.append(f"  🆔 {dup}")
        
        # Console errors
        errors = page_context.get("console_errors", [])
        if errors:
            lines.append("CONSOLE ERRORS (report these as bugs):")
            for err in errors:
                lines.append(f"  ❌ [{err.get('type', '?')}] {err.get('text', '?')}")
        
        # Network errors
        net_errors = page_context.get("network_errors", [])
        if net_errors:
            lines.append("NETWORK ERRORS (report these as bugs):")
            for err in net_errors:
                lines.append(f"  🌐 Failed: {err.get('url', '?')} ({err.get('failure', '?')})")
        
        return "\n".join(lines) if lines else "No additional page context."

