"""
GhostQA Main Agent

The core agent loop that orchestrates browser control, vision, and reasoning.
"""

import asyncio
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict, Any
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from ghostqa.config import GhostQAConfig, ScanReport
from ghostqa.browser import BrowserController
from ghostqa.vision import VisionEngine
from ghostqa.reasoning import ReasoningEngine, Action
from ghostqa.reporter import Reporter, Bug
from ghostqa.context import ScanContext
from ghostqa.run_memory import RunMemory
from ghostqa.llm import create_provider
from ghostqa.logger import setup_logger, console


class GhostQAAgent:
    """
    Main GhostQA agent that orchestrates the testing process.
    
    The agent follows this loop:
    1. OBSERVE: Capture screenshot and page state
    2. UNDERSTAND: Use vision LLM to analyze the UI
    3. DETECT: Check for bugs and issues
    4. DECIDE: Use reasoning LLM to choose next action
    5. EXECUTE: Perform the action
    6. RECORD: Save state for reproduction
    7. REPEAT until goal met or max steps reached
    """
    
    def __init__(self, config: GhostQAConfig):
        self.config = config
        self.log = setup_logger("ghostqa.agent", debug=config.debug)
        self.browser = BrowserController(config)
        
        # Create LLM provider and inject into modules
        provider = create_provider(
            provider=config.llm_provider,
            api_key=config.get_api_key(),
            model=config.model,
        )
        self.vision = VisionEngine(provider)
        self.reasoning = ReasoningEngine(provider)
        self.reporter = Reporter(config)
        
        self.history: List[Dict[str, Any]] = []
        self.run_memory = RunMemory()  # Full run context accumulator
        self.visited_states: set = set()
        self.explored_elements: List[str] = []  # Track what we've interacted with
        self.failed_targets: List[str] = []  # P1: Track failed click targets
        self.scanned_pages: set = set()  # Pages already scanned with expensive visual LLM
        self.visited_urls: set = set()  # All URLs we've navigated to
        self.steps_on_same_page: int = 0  # Counter for forced navigation
        self.last_url: str = ""
        self.test_plan: List[str] = []  # P1: Planning phase test plan
        self.test_plan_index: int = 0  # Current position in test plan
        
        # Two-phase exploration
        self.phase: str = "explore"  # 'explore' or 'test'
        self.discovered_forms: List[Dict[str, Any]] = []  # Forms found during exploration
        self.explore_budget: int = max(1, int(config.max_steps * 0.6))  # 60% for exploration
        self.explore_steps_taken: int = 0
        self.pages_scrolled: set = set()  # Pages where we've scrolled down
        self.consecutive_same_target: int = 0  # Anti-stuck counter
        self.last_target: str = ""  # Last action target for anti-stuck
        
        # Persistent scan context — same parent as reports dir
        reports_base = config.get_output_path().parent
        context_dir = reports_base / "context"
        self.scan_context = ScanContext(context_dir)
    
    async def run(self) -> ScanReport:
        """
        Execute the main agent loop.
        
        Returns:
            ScanReport with results summary
        """
        # Validate configuration
        try:
            self.config.get_api_key()
        except ValueError as e:
            raise ValueError(str(e))
        
        self.reporter.start_scan()
        
        try:
            await self.browser.start()
            console.print(f"[blue]Navigating to {self.config.url}...[/blue]")
            await self.browser.goto(self.config.url)
            self.reporter.add_page(self.config.url)
            self.log.info(f"Browser started, navigating to {self.config.url}")
            
            # Load persistent context if available (unless --fresh)
            if self.config.fresh_context:
                console.print("[yellow]🔄 Fresh scan mode — ignoring saved context[/yellow]")
            elif self.scan_context.load(self.config.url):
                # Verify the context matches the current web app (page title fingerprint)
                page_title = await self.browser.page.title() if self.browser.page else ""
                if self.scan_context.verify_fingerprint(page_title):
                    self.reporter.context_used = True
                    console.print(f"[cyan]📋 Loaded context for '{self.scan_context.app_fingerprint or page_title}' ({len(self.scan_context.pages)} pages, {len(self.scan_context.known_bugs)} bugs)[/cyan]")
                else:
                    console.print(f"[yellow]⚠️ Context mismatch! Stored: '{self.scan_context.app_fingerprint}' but found: '{page_title}'. Starting fresh.[/yellow]")
                    self.scan_context = ScanContext(self.scan_context.context_dir)
            else:
                console.print("[dim]No previous context found — starting fresh[/dim]")
            
            # P0: Auto-dismiss modals and cookie banners
            await self._auto_dismiss_modals()
            
            # P1: Planning phase — create test plan from initial screenshot
            await self._create_test_plan()
            
            consecutive_failures = 0
            last_error = None
            MAX_CONSECUTIVE_FAILURES = 5  # Allow more retries for preview models

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Exploring...", total=None)
                
                for step in range(self.config.max_steps):
                    progress.update(task, description=f"[{self.phase.upper()}] Step {step + 1}/{self.config.max_steps}")
                    
                    # Phase transition: explore → test
                    if self.phase == "explore":
                        self.explore_steps_taken += 1
                        all_plan_visited = self.test_plan_index >= len(self.test_plan) if self.test_plan else False
                        if self.explore_steps_taken >= self.explore_budget or all_plan_visited:
                            self.phase = "test"
                            if self.config.debug:
                                console.print(f"\n[bold cyan]═══ PHASE TRANSITION: EXPLORE → TEST (found {len(self.discovered_forms)} forms) ═══[/bold cyan]\n")
                    
                    try:
                        if self.config.turbo:
                            should_continue = await self._execute_step_turbo(step)
                        else:
                            should_continue = await self._execute_step(step)
                        consecutive_failures = 0  # Reset on success
                        if not should_continue:
                            console.print("[green]Exploration complete.[/green]")
                            break
                    except Exception as e:
                        consecutive_failures += 1
                        last_error = str(e)
                        if self.config.debug:
                            console.print(f"[yellow]Step {step+1} error ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {e}[/yellow]")
                        
                        # Wait before retry (handles rate limits)
                        import asyncio as _aio
                        await _aio.sleep(2)
                        
                        # Fail fast on repeated errors (e.g., invalid API key)
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            console.print(
                                f"\n[red bold]Aborting: {consecutive_failures} consecutive failures.[/red bold]\n"
                                f"[red]Last error: {last_error}[/red]"
                            )
                            break
                        continue
            
            # Generate run insights via LLM before saving context
            run_insights = {}
            try:
                provider = create_provider(
                    provider=self.config.llm_provider,
                    api_key=self.config.get_api_key(),
                    model=self.config.model,
                )
                run_insights = await self.scan_context.generate_run_insights(
                    provider=provider,
                    run_memory=self.run_memory,
                    model_used=self.config.model or self.config.llm_provider,
                    steps_taken=self.reporter.steps_taken,
                    bugs_found=len(self.reporter.bugs),
                )
                self.reporter.run_insights = run_insights
                console.print(f"[cyan]💡 Run insights generated ({len(run_insights.get('unique_findings', []))} findings, {len(run_insights.get('suggested_next_steps', []))} suggestions)[/cyan]")
            except Exception as e:
                if self.config.debug:
                    self.log.debug(f"Run insights generation failed: {e}")
            
            # Save context before browser stops
            self.scan_context.update_from_scan(
                pages_visited=self.reporter.pages_visited,
                bugs=self.reporter.bugs,
                discovered_forms=self.discovered_forms,
                explored_elements=self.explored_elements,
            )
            page_title = await self.browser.page.title() if self.browser.page else ""
            ctx_path = self.scan_context.save(
                self.config.url,
                app_fingerprint=page_title,
            )
            console.print(f"[cyan]📋 Context saved to: {ctx_path}[/cyan]")
            
            # Stop browser (finalizes video recording)
            await self.browser.stop()
            
            # Capture video path AFTER browser stops
            if self.config.record_video and self.browser.video_path:
                self.reporter._video_path = self.browser.video_path
                console.print(f"[cyan]🎥 Video saved: {self.browser.video_path}[/cyan]")
            
            # Save reports (now includes video_path)
            report_path = self.reporter.save_reports()
            console.print(f"\n[green]Report saved to: {report_path}[/green]")
            
            return ScanReport(
                url=self.config.url,
                pages_visited=len(self.reporter.pages_visited),
                bugs_found=len(self.reporter.bugs),
                report_path=str(report_path),
                duration_seconds=self.reporter.get_duration_seconds(),
                steps_taken=self.reporter.steps_taken,
            )
            
        except Exception:
            # Ensure browser is closed on error
            try:
                await self.browser.stop()
            except Exception:
                pass
            raise
    
    async def _dismiss_overlays(self) -> None:
        """Auto-dismiss common overlays (cookie banners, modals, popups) via JavaScript."""
        try:
            await self.browser.page.evaluate("""() => {
                // Click common dismiss buttons
                const dismissSelectors = [
                    // Cookie banners
                    '[class*="cookie"] button', '[id*="cookie"] button',
                    '[class*="consent"] button', '[id*="consent"] button',
                    'button[class*="accept"]', 'button[class*="agree"]',
                    'a[class*="accept"]', 'a[class*="agree"]',
                    // Generic close buttons
                    '.modal .close', '.modal [class*="close"]',
                    '[class*="popup"] [class*="close"]',
                    '[class*="overlay"] [class*="close"]',
                    'button[aria-label="Close"]', 'button[aria-label="close"]',
                    // Specific patterns
                    '.cookie-banner button', '#cookie-banner button',
                    '[class*="dismiss"]', '[class*="got-it"]',
                ];
                for (const sel of dismissSelectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        el.click();
                        break;  // One at a time to avoid side-effects
                    }
                }
            }""")
        except Exception:
            pass  # Non-critical, don't crash if overlay dismiss fails
    
    async def _execute_step(self, step: int) -> bool:
        """
        Execute a single exploration step.
        
        Returns:
            bool: True to continue, False to stop
        """
        self.reporter.increment_steps()
        
        # 0. AUTO-DISMISS common overlays (cookie banners, modals) via JS
        await self._dismiss_overlays()
        
        # 1. OBSERVE — screenshot + DOM elements
        screenshot_base64 = await self.browser.screenshot_base64()
        current_url = await self.browser.get_url()
        console_errors_before = self.browser.get_console_errors()
        
        # Track visited pages and same-page counter (include hash for SPAs)
        self.reporter.add_page(current_url)
        self.visited_urls.add(current_url)
        # Normalize: strip query params but KEEP hash for SPA route detection
        url_normalized = current_url.split("?")[0]
        if url_normalized == self.last_url:
            self.steps_on_same_page += 1
        else:
            self.steps_on_same_page = 0
            self.last_url = url_normalized
        
        # 2. UNDERSTAND — Vision LLM + DOM extraction
        page_analysis = await self.vision.analyze_page(
            screenshot_base64,
            context=self.config.context,
        )
        
        # Enrich page analysis with real DOM elements
        dom_elements = await self._get_dom_elements()
        page_analysis["dom_links"] = dom_elements.get("links", [])
        page_analysis["dom_buttons"] = dom_elements.get("buttons", [])
        page_analysis["dom_forms"] = dom_elements.get("forms", [])
        
        # Track discovered forms for Phase 2 security testing
        if self.phase == "explore" and dom_elements.get("forms"):
            for form in dom_elements["forms"]:
                form_entry = {"url": current_url, "fields": form.get("fields", [])}
                # Avoid duplicate form entries
                if form_entry not in self.discovered_forms:
                    self.discovered_forms.append(form_entry)
        
        if self.config.debug:
            n_links = len(dom_elements.get('links', []))
            n_buttons = len(dom_elements.get('buttons', []))
            n_forms = len(dom_elements.get('forms', []))
            self.log.debug(f"Page: {page_analysis.get('page_type')} | DOM: {n_links} links, {n_buttons} buttons, {n_forms} forms")
        
        # 3. DETECT bugs
        # Only run expensive visual LLM scan on pages not yet deep-scanned
        is_new_page = url_normalized not in self.scanned_pages
        if is_new_page:
            await self._detect_bugs(
                screenshot_base64, current_url, page_analysis,
                deep_scan=True,
            )
            self.scanned_pages.add(url_normalized)
            # Save screenshot to context for visual change detection
            page_path = urlparse(current_url).fragment or urlparse(current_url).path or "home"
            self.scan_context.save_page_screenshot(
                self.config.url, page_path, screenshot_base64
            )
        else:
            # Cheap text-only bug check on already-scanned pages
            await self._detect_bugs(
                screenshot_base64, current_url, page_analysis,
                deep_scan=False,
            )
        
        # Check for console errors
        new_errors = self.browser.get_console_errors()[len(console_errors_before):]
        if new_errors:
            self._report_console_errors(new_errors, current_url)
        
        # 4. FORCE NAVIGATION if stuck on same page too long
        # BUT: don't force nav if we're mid-form (last action was fill)
        action = None
        last_action_type = self.history[-1].get("action", {}).get("action_type") if self.history else None
        is_mid_form = last_action_type in ("fill", "press")
        has_forms = len(dom_elements.get("forms", [])) > 0
        nav_threshold = 6 if (is_mid_form or has_forms) else 4  # Give forms more time
        
        if self.steps_on_same_page >= nav_threshold and not is_mid_form:
            # Try test plan item first, then DOM links
            action = self._get_next_test_plan_action()
            if not action:
                action = await self._pick_unvisited_link(dom_elements)
            if action:
                self.steps_on_same_page = 0
                if self.config.debug:
                    self.log.debug(f"[FORCED] {action.reasoning}")
        
        # 4b. FORCED SCROLL on first visit to a page
        if action is None:
            url_for_scroll = urlparse(current_url).fragment or urlparse(current_url).path or "/"
            if url_for_scroll not in self.pages_scrolled:
                self.pages_scrolled.add(url_for_scroll)
                action = Action(
                    action_type="scroll",
                    target="down",
                    reasoning=f"First visit to {url_for_scroll} — scrolling to see below-fold content",
                )
                if self.config.debug:
                    self.log.debug(f"[SCROLL] Auto-scroll on new page: {url_for_scroll}")
        
        # 4c. ANTI-STUCK: if agent targets same element 2+ times, force nav
        if action is None and self.consecutive_same_target >= 2:
            action = await self._pick_unvisited_link(dom_elements)
            if action:
                self.consecutive_same_target = 0
                if self.config.debug:
                    self.log.debug(f"[ANTI-STUCK] Same target {self.consecutive_same_target}x → forced nav: {action.target}")
        
        # 5. DECIDE next action (normal path if not forced)
        if action is None:
            action = await self.reasoning.decide_next_action(
                page_analysis=page_analysis,
                history=self.history,
                screenshot_base64=screenshot_base64,
                explored_elements=self.explored_elements,
                failed_targets=self.failed_targets,
                phase=self.phase,
                discovered_forms=self.discovered_forms,
                context_summary=self.scan_context.get_context_summary(),
                run_memory=self.run_memory,
            )
        
        if self.config.debug:
            self.log.debug(f"[{self.phase.upper()}] Action: {action.action_type} → {action.target}")
            self.log.debug(f"  Reason: {action.reasoning}")
            if action.thinking:
                # Show thinking in a readable way
                thinking_preview = action.thinking[:200] + "..." if len(action.thinking) > 200 else action.thinking
                self.log.debug(f"  💭 Thinking: {thinking_preview}")
        
        # Check if exploration is done
        if action.action_type == "done":
            if self.phase == "explore":
                # Explore done → transition to test phase
                self.phase = "test"
                if self.config.debug:
                    from rich.console import Console as _C
                    _C().print(f"\n[bold cyan]═══ EXPLORE COMPLETE → Starting security tests ({len(self.discovered_forms)} forms) ═══[/bold cyan]\n")
                if not self.discovered_forms:
                    return False  # No forms found, nothing to test
                return True  # Continue to test phase
            else:
                return False  # Test phase done → stop scan
        
        # 5. EXECUTE
        success = await self._execute_action(action)
        
        # Auto-dismiss modals after navigation to a new page
        if action.action_type == "navigate" and success:
            await self._auto_dismiss_modals()
        
        # 6. RECORD
        self.history.append({
            "step": step,
            "url": current_url,
            "action": {
                "action_type": action.action_type,
                "target": action.target,
                "value": action.value,
            },
            "result": "success" if success else "failed",
        })
        
        # Check for new console errors after action
        new_errors_after = self.browser.get_console_errors()[len(console_errors_before):]
        if len(new_errors_after) > len(new_errors):
            self._report_console_errors(
                new_errors_after[len(new_errors):],
                current_url,
                action=action,
            )
        
        # Track explored elements to avoid repeats
        if action.target and action.action_type in ("click", "fill"):
            self.explored_elements.append(f"{action.action_type}: {action.target}")
        
        # Anti-stuck: track consecutive same-target actions
        current_target = f"{action.action_type}:{action.target}" if action.target else ""
        if current_target and current_target == self.last_target:
            self.consecutive_same_target += 1
        else:
            self.consecutive_same_target = 0
        self.last_target = current_target
        
        # P1: Track failed targets so reasoning engine avoids them
        if not success and action.target and action.action_type == "click":
            self.failed_targets.append(action.target)
        
        # Record step in full run memory
        bugs_this_step = [b.description for b in self.reporter.bugs[-3:]]  # recent bugs
        self.run_memory.record_step(
            step_number=step + 1,
            url=current_url,
            action_type=action.action_type,
            target=action.target or "",
            value=action.value or "",
            reasoning=action.reasoning or "",
            success=success,
            bugs_found=bugs_this_step if bugs_this_step else [],
            observations=action.thinking or "",
        )
        
        # Compress run memory periodically
        if self.run_memory.needs_compression():
            provider = create_provider(
                provider=self.config.llm_provider,
                api_key=self.config.get_api_key(),
                model=self.config.model,
            )
            await self.run_memory.compress(provider)
            if self.config.debug:
                self.log.debug(f"📝 Run memory compressed (covers steps 1-{self.run_memory.last_compressed_step})")
        
        # Capture credentials from fill actions (email/password fields)
        if action.action_type == "fill" and action.value and action.target:
            target_lower = (action.target or "").lower()
            if "email" in target_lower or "user" in target_lower:
                self._last_filled_email = action.value
            elif "password" in target_lower or "pass" in target_lower:
                self._last_filled_password = action.value
        
        # Detect successful registration/login by URL change after click on submit
        if action.action_type == "click" and success:
            target_lower = (action.target or "").lower()
            if any(kw in target_lower for kw in ("register", "log in", "login", "sign up", "submit")):
                email = getattr(self, "_last_filled_email", None)
                password = getattr(self, "_last_filled_password", None)
                if email and password:
                    # Determine how the credential was obtained
                    is_sqli = any(p in (email + password).lower() for p in ("' or", "1=1", "--", "union", "' or '"))
                    is_xss = "<script" in (email + password).lower()
                    if is_sqli:
                        obtained_via = "sql_injection"
                    elif is_xss:
                        obtained_via = "xss_exploitation"
                    elif "register" in target_lower or "sign up" in target_lower:
                        obtained_via = "registration"
                    else:
                        obtained_via = "login"
                    self.scan_context.add_credential(email, password, status="working", obtained_via=obtained_via)
                    if self.config.debug:
                        self.log.debug(f"💾 Saved credential ({obtained_via}): {email}")
                    self._last_filled_email = None
                    self._last_filled_password = None
        
        return True
    
    async def _execute_step_turbo(self, step: int) -> bool:
        """
        TURBO MODE: Execute a step with 1 unified LLM call instead of 2.
        
        Flow: screenshot + DOM → single LLM call → action + bugs → execute
        """
        self.reporter.increment_steps()
        
        # 0. Auto-dismiss overlays
        await self._dismiss_overlays()
        
        # 1. OBSERVE — screenshot + DOM
        screenshot_base64 = await self.browser.screenshot_base64()
        current_url = await self.browser.get_url()
        console_errors_before = self.browser.get_console_errors()
        
        # Track pages
        self.reporter.add_page(current_url)
        self.visited_urls.add(current_url)
        url_normalized = current_url.split("?")[0]
        if url_normalized == self.last_url:
            self.steps_on_same_page += 1
        else:
            self.steps_on_same_page = 0
            self.last_url = url_normalized
        
        # Get DOM elements and remap keys for _render_dom_elements compatibility
        raw_dom = await self._get_dom_elements()
        dom_elements = {
            "dom_links": raw_dom.get("links", []),
            "dom_buttons": raw_dom.get("buttons", []),
            "dom_forms": raw_dom.get("forms", []),
        }
        
        # Get rich page context (headings, images, accessibility issues, console errors)
        page_context = await self.browser.get_page_context()
        if self.config.debug:
            dom_stats = f"DOM: {len(raw_dom.get('links', []))} links, {len(raw_dom.get('buttons', []))} buttons, {len(raw_dom.get('forms', []))} forms"
            self.log.debug(f"  Page: {page_context.get('title', '?')} | {dom_stats}")
            # Count all DOM-detected issues
            issue_counts = []
            for key, label in [
                ("accessibility_issues", "♿ a11y"),
                ("layout_issues", "📐 layout"),
                ("broken_links", "🔗 dead links"),
                ("tiny_text", "🔍 tiny text"),
                ("security_issues", "🔒 security"),
                ("duplicate_ids", "🆔 dup IDs"),
                ("console_errors", "❌ console"),
                ("network_errors", "🌐 network"),
            ]:
                count = len(page_context.get(key, []))
                if count > 0:
                    issue_counts.append(f"{label}: {count}")
            if issue_counts:
                self.log.debug(f"  DOM issues: {', '.join(issue_counts)}")
        
        # Track forms
        if self.phase == "explore" and raw_dom.get("forms"):
            for form in raw_dom["forms"]:
                form_entry = {"url": current_url, "fields": form.get("fields", [])}
                if form_entry not in self.discovered_forms:
                    self.discovered_forms.append(form_entry)
        
        # 2. FORCED actions (scroll, anti-stuck nav)
        action = None
        last_action_type = self.history[-1].get("action", {}).get("action_type") if self.history else None
        is_mid_form = last_action_type in ("fill", "press", "fill_form")
        has_forms = len(raw_dom.get("forms", [])) > 0
        nav_threshold = 8 if (is_mid_form or has_forms) else 6  # Turbo: more time per page
        
        if self.steps_on_same_page >= nav_threshold and not is_mid_form:
            action = self._get_next_test_plan_action()
            if not action:
                action = await self._pick_unvisited_link(raw_dom)
            if action:
                self.steps_on_same_page = 0
        
        # Forced scroll ONLY on truly new page (and only once)
        if action is None:
            url_for_scroll = urlparse(current_url).fragment or urlparse(current_url).path or "/"
            if url_for_scroll not in self.pages_scrolled:
                self.pages_scrolled.add(url_for_scroll)
                action = Action(
                    action_type="scroll", target="down",
                    reasoning=f"First visit to {url_for_scroll} — scrolling to reveal full page",
                )
        
        # Anti-stuck: if repeating same target
        if action is None and self.consecutive_same_target >= 2:
            action = await self._pick_unvisited_link(raw_dom)
            if action:
                self.consecutive_same_target = 0
        
        # Anti-scroll-loop: if LLM scrolled 2+ times recently, force a click/navigate
        if action is None:
            recent_scrolls = sum(
                1 for h in self.history[-3:]
                if h.get('action', {}).get('action_type') == 'scroll'
            ) if self.history else 0
            if recent_scrolls >= 2:
                action = await self._pick_unvisited_link(raw_dom)
                if action and self.config.debug:
                    self.log.debug(f"  🔄 Anti-scroll-loop: forcing navigation to {action.target}")

        
        # 3. UNIFIED LLM CALL — vision + reasoning + bug detection (with page context)
        bugs_from_llm = []
        if action is None:
            action, bugs_from_llm = await self.reasoning.decide_and_analyze(
                screenshot_base64=screenshot_base64,
                dom_elements=dom_elements,
                history=self.history,
                explored_elements=self.explored_elements,
                failed_targets=self.failed_targets,
                goal=self.config.context,
                phase=self.phase,
                discovered_forms=self.discovered_forms,
                context_summary=self.scan_context.get_context_summary(),
                page_context=page_context,
                run_memory=self.run_memory,
            )
        
        # Report bugs from unified call
        for bug_data in bugs_from_llm:
            desc = bug_data.get("description", "")
            if not desc or self._is_self_inflicted(desc.lower()):
                continue
            bug = Bug(
                bug_type=bug_data.get("type", "visual"),
                severity=bug_data.get("severity", "medium"),
                description=desc,
                url=current_url,
                steps=self._get_reproduction_steps(),
                confidence=0.8,
            )
            self.reporter.add_bug(bug)
        
        if self.config.debug:
            self.log.debug(f"[TURBO] Action: {action.action_type} → {action.target}")
            self.log.debug(f"  Reason: {action.reasoning}")
            if bugs_from_llm:
                self.log.debug(f"  🐛 Bugs found this step: {len(bugs_from_llm)}")
            if action.thinking:
                thinking_preview = action.thinking[:200] + "..." if len(action.thinking) > 200 else action.thinking
                self.log.debug(f"  💭 Thinking: {thinking_preview}")
        
        # Check if done
        if action.action_type == "done":
            if self.phase == "explore":
                self.phase = "test"
                if not self.discovered_forms:
                    return False
                return True
            else:
                return False
        
        # Console errors
        new_errors = self.browser.get_console_errors()[len(console_errors_before):]
        if new_errors:
            self._report_console_errors(new_errors, current_url)
        
        # 4. EXECUTE
        success = await self._execute_action(action)
        
        # Track history
        self.history.append({
            "step": step + 1,
            "url": current_url,
            "action": {
                "action_type": action.action_type,
                "target": action.target,
                "value": action.value,
                "reasoning": action.reasoning,
                "fields": action.fields,
            },
            "success": success,
        })
        
        # Track explored elements
        if action.target and action.action_type in ("click", "fill", "fill_form"):
            self.explored_elements.append(f"{action.action_type}: {action.target}")
        
        # Anti-stuck tracking
        current_target = f"{action.action_type}:{action.target}" if action.target else ""
        if current_target and current_target == self.last_target:
            self.consecutive_same_target += 1
        else:
            self.consecutive_same_target = 0
        self.last_target = current_target
        
        if not success and action.target and action.action_type == "click":
            self.failed_targets.append(action.target)
        
        # Record step in full run memory
        bugs_this_step = [b.get("description", "") for b in bugs_from_llm] if bugs_from_llm else []
        self.run_memory.record_step(
            step_number=step + 1,
            url=current_url,
            action_type=action.action_type,
            target=action.target or "",
            value=action.value or "",
            reasoning=action.reasoning or "",
            success=success,
            bugs_found=bugs_this_step,
            observations=action.thinking or "",
        )
        
        # Compress run memory periodically
        if self.run_memory.needs_compression():
            provider = create_provider(
                provider=self.config.llm_provider,
                api_key=self.config.get_api_key(),
                model=self.config.model,
            )
            await self.run_memory.compress(provider)
            if self.config.debug:
                self.log.debug(f"📝 Run memory compressed (covers steps 1-{self.run_memory.last_compressed_step})")
        
        # Credential tracking
        if action.action_type in ("fill", "fill_form") and action.value and action.target:
            target_lower = (action.target or "").lower()
            if "email" in target_lower or "user" in target_lower:
                self._last_filled_email = action.value
            elif "password" in target_lower or "pass" in target_lower:
                self._last_filled_password = action.value
        
        return True
    
    async def _execute_action(self, action: Action) -> bool:
        """Execute an action and return success status."""
        try:
            if action.action_type == "click":
                target = action.target or ""
                # Skip empty targets entirely
                if not target.strip():
                    return False
                # Try clicking by text first, then by CSS selector
                success = await self.browser.click_text(target)
                if not success:
                    success = await self.browser.click(target)
                return success
            
            elif action.action_type == "fill":
                return await self.browser.fill(action.target, action.value or "")
            
            elif action.action_type == "navigate":
                target_url = action.target
                # Resolve relative URLs against current page
                if target_url and not target_url.startswith(("http://", "https://")):
                    current = await self.browser.get_url()
                    target_url = urljoin(current, target_url)
                await self.browser.goto(target_url)
                return True
            
            elif action.action_type == "scroll":
                await self.browser.scroll(action.target or "down")
                return True
            
            elif action.action_type == "press":
                await self.browser.press(action.target or "Enter")
                return True
            
            elif action.action_type == "wait":
                await asyncio.sleep(1)
                return True
            
            elif action.action_type == "fill_form":
                # Batch: fill all fields, then click submit
                if action.fields:
                    for field in action.fields:
                        field_target = field.get("target", "")
                        field_value = field.get("value", "")
                        if field_target and field_value:
                            try:
                                await self.browser.fill(field_target, field_value)
                                self.explored_elements.append(f"fill: {field_target}")
                                # Track for credential capture
                                tl = field_target.lower()
                                if "email" in tl or "user" in tl:
                                    self._last_filled_email = field_value
                                elif "password" in tl or "pass" in tl:
                                    self._last_filled_password = field_value
                            except Exception as e:
                                if self.config.debug:
                                    self.log.debug(f"fill_form field failed: {field_target} → {e}")
                    await asyncio.sleep(0.3)  # Brief pause before submit
                # Click submit button
                if action.target:
                    try:
                        await self.browser.click(action.target)
                    except Exception:
                        if self.config.debug:
                            self.log.debug(f"fill_form submit click failed: {action.target}")
                return True
            
            return False
            
        except Exception as e:
            if self.config.debug:
                console.print(f"[yellow]Action failed: {e}[/yellow]")
            return False
    
    async def _detect_bugs(
        self,
        screenshot_base64: str,
        url: str,
        page_analysis: Dict[str, Any],
        deep_scan: bool = True,
    ) -> None:
        """Detect bugs from current page state.
        
        Args:
            deep_scan: If True, runs the expensive visual LLM bug scan.
                       Set to False for pages already deep-scanned.
        """
        # Always check visible issues from page analysis (cheap, from existing LLM call)
        visible_issues = page_analysis.get("visible_issues", [])
        for issue in visible_issues:
            if isinstance(issue, str) and issue.strip():
                bug = Bug(
                    bug_type="visual",
                    severity="medium",
                    description=issue,
                    url=url,
                    steps=self._get_reproduction_steps(),
                )
                self.reporter.add_bug(bug)
        
        # Run expensive visual bug detection ONLY on first visit
        if deep_scan and self.config.check_visual_issues:
            try:
                visual_bugs = await self.vision.detect_visual_bugs(screenshot_base64)
                for vbug in visual_bugs:
                    confidence = float(vbug.get("confidence", 0.8))
                    # Skip very low confidence bugs (likely observations, not bugs)
                    if confidence < 0.4:
                        continue
                    # Skip bugs that describe the agent's own test inputs
                    desc_lower = vbug.get("description", "").lower()
                    if self._is_self_inflicted(desc_lower):
                        continue
                    bug = Bug(
                        bug_type=vbug.get("type", "visual"),
                        severity=vbug.get("severity", "medium"),
                        description=vbug.get("description", "Visual issue detected"),
                        url=url,
                        steps=self._get_reproduction_steps(),
                        confidence=confidence,
                    )
                    self.reporter.add_bug(bug)
            except Exception:
                pass  # Vision bug detection is optional
        
        # P2: DOM-based accessibility checks on first visit
        if deep_scan:
            await self._detect_dom_bugs(url)
    
    def _is_self_inflicted(self, description: str) -> bool:
        """Check if a bug description is about the agent's own test inputs."""
        # Collect all values the agent has typed into forms
        agent_inputs = set()
        for entry in self.history:
            action = entry.get("action", {})
            if action.get("action_type") in ("fill", "fill_form") and action.get("value"):
                agent_inputs.add(action["value"].lower())
            # Also check fill_form fields
            if action.get("fields"):
                for field in action["fields"]:
                    if field.get("value"):
                        agent_inputs.add(field["value"].lower())
        
        # Check if the bug description mentions any agent test input
        test_patterns = ["pre-filled", "prefilled", "pre filled", "contains sql", "contains xss",
                         "injection pattern", "script tag", "already filled", "test data in"]
        if any(p in description for p in test_patterns):
            return True
        
        # Check if the description mentions specific payloads the agent used
        for inp in agent_inputs:
            if len(inp) > 5 and inp in description:
                return True
        
        return False
    
    def _report_console_errors(
        self,
        errors: List[Dict],
        url: str,
        action: Optional[Action] = None,
    ) -> None:
        """Report console errors as bugs."""
        for error in errors:
            if error.get("type") == "error" or error.get("type") == "exception":
                description = f"Console error: {error.get('text', 'Unknown error')[:200]}"
                if action:
                    description += f" (after {action.action_type} on '{action.target}')"
                
                bug = Bug(
                    bug_type="console_error",
                    severity="high" if error.get("type") == "exception" else "medium",
                    description=description,
                    url=url,
                    steps=self._get_reproduction_steps(),
                    console_errors=[error],
                )
                self.reporter.add_bug(bug)
    
    def _get_reproduction_steps(self) -> List[str]:
        """Build reproduction steps from history — only steps since last page navigation."""
        steps = []
        scroll_count = 0
        
        # Find the last navigate action to determine the current page context
        last_nav_index = -1
        for i in range(len(self.history) - 1, -1, -1):
            action = self.history[i].get("action", {})
            if action.get("action_type") == "navigate":
                last_nav_index = i
                break
        
        # Start from the last navigation (or beginning if none)
        start_index = max(0, last_nav_index)
        relevant_history = self.history[start_index:]
        
        # Always start with a navigate step
        if relevant_history:
            first_action = relevant_history[0].get("action", {})
            if first_action.get("action_type") == "navigate":
                steps.append(f"Navigate to {first_action.get('target', self.config.url)}")
                relevant_history = relevant_history[1:]  # Skip the navigate in the loop
            else:
                steps.append(f"Navigate to {self.config.url}")
        else:
            steps.append(f"Navigate to {self.config.url}")
        
        for item in relevant_history:
            action = item.get("action", {})
            action_type = action.get("action_type", "")
            target = action.get("target", "") or ""
            value = action.get("value", "")
            
            if action_type == "click":
                if scroll_count > 1:
                    steps.append(f"Scroll down ({scroll_count} times)")
                scroll_count = 0
                if target.strip():
                    steps.append(f"Click on '{target}'")
            elif action_type == "fill":
                scroll_count = 0
                steps.append(f"Fill '{target}' with '{value}'")
            elif action_type == "navigate":
                # Another navigation within the same context — include it
                scroll_count = 0
                steps.append(f"Navigate to {target}")
            elif action_type == "scroll":
                scroll_count += 1
                if scroll_count == 1:
                    steps.append(f"Scroll {target or 'down'}")
            elif action_type == "press":
                scroll_count = 0
                steps.append(f"Press {target}")
        
        return steps

    async def _get_dom_elements(self) -> Dict[str, Any]:
        """Extract interactive elements from the real DOM (not screenshot-based)."""
        try:
            return await self.browser.get_interactive_elements()
        except Exception:
            return {"links": [], "forms": [], "buttons": []}

    async def _pick_unvisited_link(self, dom_elements: Dict[str, Any]) -> Optional[Action]:
        """Pick an unvisited link from the DOM for forced navigation."""
        links = dom_elements.get("links", [])
        current_url = await self.browser.get_url()
        base_domain = current_url.split("//")[-1].split("/")[0]  # e.g. localhost:3000

        # Priority navigation targets for testing
        priority_paths = ["/login", "/register", "/search", "/contact", "/about", "/admin", "/profile", "/account"]

        # First try priority paths
        for path in priority_paths:
            for link in links:
                href = link.get("href", "")
                if path in href.lower() and href not in self.visited_urls:
                    return Action(
                        action_type="navigate",
                        target=href,
                        reasoning=f"Forced navigation to priority page: {path}",
                    )

        # Then try any unvisited link on the same domain
        for link in links:
            href = link.get("href", "")
            text = link.get("text", "").strip()
            if (
                href
                and base_domain in href
                and href not in self.visited_urls
                and href != current_url
            ):
                label = text or href.split("/")[-1] or href
                return Action(
                    action_type="navigate",
                    target=href,
                    reasoning=f"Forced navigation to unvisited page: {label}",
                )

        return None

    async def _auto_dismiss_modals(self) -> None:
        """P0: Auto-dismiss common modals, cookie banners, and popups."""
        await asyncio.sleep(1)  # Wait for modals to appear
        dismiss_patterns = [
            "text=Dismiss", "text=Got it", "text=Accept", "text=I agree",
            "text=OK", "text=Close", "text=No thanks", "text=Maybe later",
            "[aria-label='Close']", "[aria-label='Dismiss']",
            "button.close", ".cookie-accept", ".dismiss",
        ]
        dismissed = 0
        for pattern in dismiss_patterns:
            try:
                if pattern.startswith("text="):
                    success = await self.browser.click_text(pattern.replace("text=", ""))
                else:
                    success = await self.browser.click(pattern)
                if success:
                    dismissed += 1
                    await asyncio.sleep(0.5)
            except Exception:
                continue
        if dismissed and self.config.debug:
            self.log.debug(f"[AUTO] Dismissed {dismissed} modal(s)/banner(s)")

    async def _create_test_plan(self) -> None:
        """P1: LLM creates a test plan from the initial page."""
        screenshot_b64 = await self.browser.screenshot_base64()
        dom = await self._get_dom_elements()
        url = await self.browser.get_url()
        links = [f"  - [{l.get('text','').strip()}]({l.get('href','')})"
                 for l in dom.get("links", [])[:20]
                 if l.get("text","").strip() and l.get("href","")]
        from ghostqa.llm.base import Message
        from ghostqa.utils import parse_json_response
        messages = [
            Message(role="system", content="""You are a QA test planner. Given a web app's screenshot and DOM links,
return 5-8 URLs to visit for thorough testing. Focus on login, register, search, cart, profile, admin.
Respond in JSON: {"test_plan": ["url1", "url2", ...]}
Return ONLY URLs from the provided links list. Do NOT invent URLs."""),
            Message(role="user", content=f"Plan for: {url}\nLinks:\n" + chr(10).join(links)),
        ]
        try:
            resp = await self.reasoning.provider.chat_with_image(
                messages=messages, image_base64=screenshot_b64,
                json_mode=True, max_tokens=500,
            )
            result = parse_json_response(resp.content)
            self.test_plan = result.get("test_plan", [])
            if self.config.debug and self.test_plan:
                self.log.debug(f"[PLAN] {len(self.test_plan)} items: {self.test_plan}")
        except Exception as e:
            if self.config.debug:
                self.log.debug(f"[PLAN] Failed: {e}")
            self.test_plan = []

    def _get_next_test_plan_action(self) -> Optional[Action]:
        """Get the next unvisited URL from the test plan."""
        while self.test_plan_index < len(self.test_plan):
            target = self.test_plan[self.test_plan_index]
            self.test_plan_index += 1
            if target not in self.visited_urls:
                return Action(
                    action_type="navigate", target=target,
                    reasoning=f"Test plan step {self.test_plan_index}: {target}",
                )
        return None

    async def _detect_dom_bugs(self, url: str) -> None:
        """P2: Detect accessibility bugs from DOM structure."""
        try:
            page = self.browser.page
            missing_alt = await page.evaluate("""() => {
                return document.querySelectorAll('img:not([alt]), img[alt=""]').length;
            }""")
            if missing_alt > 0:
                self.reporter.add_bug(Bug(
                    bug_type="accessibility", severity="medium",
                    description=f"{missing_alt} image(s) missing alt text (WCAG 1.1.1)",
                    url=url, steps=self._get_reproduction_steps(),
                ))
            empty_links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a')).filter(a => {
                    return !a.innerText.trim() && !a.getAttribute('aria-label')
                           && !a.getAttribute('title') && a.offsetWidth > 0;
                }).length;
            }""")
            if empty_links > 0:
                self.reporter.add_bug(Bug(
                    bug_type="accessibility", severity="low",
                    description=f"{empty_links} link(s) with no accessible name (WCAG 2.4.4)",
                    url=url, steps=self._get_reproduction_steps(),
                ))
            unlabeled = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll(
                    'input:not([type="hidden"]), textarea, select'
                )).filter(el => {
                    const id = el.id;
                    const hasLabel = id ? !!document.querySelector('label[for="'+id+'"]') : false;
                    return !hasLabel && !el.getAttribute('aria-label') && el.offsetWidth > 0;
                }).length;
            }""")
            if unlabeled > 0:
                self.reporter.add_bug(Bug(
                    bug_type="accessibility", severity="medium",
                    description=f"{unlabeled} form input(s) without labels (WCAG 1.3.1)",
                    url=url, steps=self._get_reproduction_steps(),
                ))
        except Exception:
            pass

