"""
GhostQA Browser Controller

Playwright-based browser automation for web application testing.
"""

import asyncio
from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from typing import Optional, List, Dict, Any
from pathlib import Path
import base64
from datetime import datetime

from ghostqa.config import GhostQAConfig


class BrowserController:
    """Controls browser interactions using Playwright."""
    
    def __init__(self, config: GhostQAConfig):
        self.config = config
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._console_errors: List[Dict[str, Any]] = []
        self._network_errors: List[Dict[str, Any]] = []
        self._video_path: Optional[str] = None
    
    async def start(self) -> None:
        """Launch browser and create a new page."""
        self._playwright = await async_playwright().start()
        
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
        )
        
        context_options = {
            "viewport": {
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
        }
        
        if self.config.record_video:
            video_dir = Path(self.config.output_dir or "reports") / "videos"
            video_dir.mkdir(parents=True, exist_ok=True)
            context_options["record_video_dir"] = str(video_dir)
            context_options["record_video_size"] = {
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            }
        
        self._context = await self._browser.new_context(**context_options)
        
        self._page = await self._context.new_page()
        
        # Set up console and network error listeners
        self._page.on("console", self._handle_console)
        self._page.on("pageerror", self._handle_page_error)
        self._page.on("requestfailed", self._handle_request_failed)
        
        # Set timeout
        self._page.set_default_timeout(self.config.timeout)
    
    async def stop(self) -> None:
        """Close browser and clean up resources."""
        if self._page and self.config.record_video:
            try:
                video = self._page.video
                if video:
                    await self._page.close()  # Must close page before getting video path
                    self._video_path = await video.path()
                    self._page = None
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    @property
    def video_path(self) -> Optional[str]:
        """Return the path to the recorded video (only after stop())."""
        return self._video_path
    
    async def goto(self, url: str) -> None:
        """Navigate to a URL. Falls back to domcontentloaded if networkidle times out."""
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=15000)
        except Exception:
            # Some sites (animations, analytics) never go network-idle
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
    
    async def screenshot(self, full_page: bool = False) -> bytes:
        """Capture a screenshot of the current page."""
        return await self._page.screenshot(full_page=full_page)
    
    async def screenshot_base64(self, full_page: bool = False) -> str:
        """Capture a screenshot and return as base64 string."""
        screenshot_bytes = await self.screenshot(full_page=full_page)
        return base64.b64encode(screenshot_bytes).decode("utf-8")
    
    async def save_screenshot(self, path: Path) -> None:
        """Save a screenshot to a file."""
        await self._page.screenshot(path=str(path))
    
    async def click(self, selector: str) -> bool:
        """Click an element by selector."""
        try:
            await self._page.click(selector, timeout=5000)
            await self._wait_for_stable()
            return True
        except Exception as e:
            return False
    
    async def click_text(self, text: str) -> bool:
        """Click an element by its text content."""
        try:
            await self._page.click(f"text={text}", timeout=5000)
            await self._wait_for_stable()
            return True
        except Exception:
            return False
    
    async def fill(self, selector: str, value: str) -> bool:
        """Fill a form field."""
        try:
            await self._page.fill(selector, value, timeout=5000)
            return True
        except Exception:
            return False
    
    async def type_text(self, selector: str, value: str, delay: int = 50) -> bool:
        """Type text into a field with delay (more human-like)."""
        try:
            await self._page.type(selector, value, delay=delay)
            return True
        except Exception:
            return False
    
    async def press(self, key: str) -> None:
        """Press a keyboard key."""
        await self._page.keyboard.press(key)
    
    async def scroll(self, direction: str = "down", amount: int = 800) -> None:
        """Scroll the page."""
        if direction == "down":
            await self._page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            await self._page.evaluate(f"window.scrollBy(0, -{amount})")
    
    async def get_url(self) -> str:
        """Get the current URL."""
        return self._page.url
    
    async def get_title(self) -> str:
        """Get the page title."""
        return await self._page.title()
    
    async def get_html(self) -> str:
        """Get the full page HTML."""
        return await self._page.content()
    
    async def get_text(self) -> str:
        """Get all visible text on the page."""
        return await self._page.inner_text("body")
    
    async def get_links(self) -> List[Dict[str, str]]:
        """Get all links on the page."""
        links = await self._page.evaluate("""
            () => {
                const links = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    links.push({
                        href: a.href,
                        text: a.innerText.trim(),
                    });
                });
                return links;
            }
        """)
        return links
    
    async def get_forms(self) -> List[Dict[str, Any]]:
        """Get all forms on the page with their inputs."""
        forms = await self._page.evaluate("""
            () => {
                const forms = [];
                document.querySelectorAll('form').forEach((form, idx) => {
                    const inputs = [];
                    form.querySelectorAll('input, textarea, select').forEach(input => {
                        inputs.push({
                            type: input.type || input.tagName.toLowerCase(),
                            name: input.name,
                            id: input.id,
                            placeholder: input.placeholder,
                            required: input.required,
                        });
                    });
                    forms.push({
                        id: form.id || `form_${idx}`,
                        action: form.action,
                        method: form.method,
                        inputs: inputs,
                    });
                });
                return forms;
            }
        """)
        return forms
    
    async def get_buttons(self) -> List[Dict[str, str]]:
        """Get all clickable buttons on the page."""
        buttons = await self._page.evaluate("""
            () => {
                const buttons = [];
                const selectors = 'button, [role="button"], input[type="submit"], input[type="button"], a.btn, a.button';
                document.querySelectorAll(selectors).forEach((btn, idx) => {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        buttons.push({
                            text: btn.innerText.trim() || btn.value || btn.title,
                            id: btn.id,
                            type: btn.type || 'button',
                            selector: btn.id ? `#${btn.id}` : `button:nth-of-type(${idx + 1})`,
                        });
                    }
                });
                return buttons;
            }
        """)
        return buttons
    
    async def get_interactive_elements(self) -> Dict[str, Any]:
        """Get all interactive elements on the page."""
        return {
            "links": await self.get_links(),
            "forms": await self.get_forms(),
            "buttons": await self.get_buttons(),
        }
    
    async def get_page_context(self) -> Dict[str, Any]:
        """
        Extract a rich semantic DOM snapshot in a single JS call.
        
        Returns page title, meta description, headings, images (with alt/dimensions),
        accessibility issues, and structural data that complements the visual screenshot.
        """
        try:
            context = await self._page.evaluate("""() => {
                const result = {
                    title: document.title || '',
                    meta_description: '',
                    headings: [],
                    images: [],
                    accessibility_issues: [],
                    visible_text_snippet: '',
                    has_main_landmark: false,
                    has_nav_landmark: false,
                    lang: document.documentElement.lang || 'not set',
                };

                // Meta description
                const metaDesc = document.querySelector('meta[name="description"]');
                if (metaDesc) result.meta_description = metaDesc.content || '';

                // Headings (h1-h4)
                const headings = document.querySelectorAll('h1, h2, h3, h4');
                const seenHeadings = new Set();
                headings.forEach(h => {
                    const text = h.innerText.trim();
                    if (text && !seenHeadings.has(text) && result.headings.length < 15) {
                        seenHeadings.add(text);
                        result.headings.push({
                            level: h.tagName,
                            text: text.substring(0, 80)
                        });
                    }
                });

                // Images (with accessibility audit)
                let missingAlt = 0;
                let emptyAlt = 0;
                let brokenImages = 0;
                const imgs = document.querySelectorAll('img');
                imgs.forEach((img, idx) => {
                    if (idx >= 20) return;  // Cap at 20
                    const rect = img.getBoundingClientRect();
                    const hasAlt = img.hasAttribute('alt');
                    const altText = img.alt || '';
                    const isBroken = !img.complete || img.naturalWidth === 0;
                    
                    if (!hasAlt) missingAlt++;
                    else if (altText === '') emptyAlt++;
                    if (isBroken && img.src) brokenImages++;

                    // Only include visible images
                    if (rect.width > 10 && rect.height > 10) {
                        result.images.push({
                            src: img.src.substring(img.src.lastIndexOf('/') + 1).substring(0, 60),
                            alt: hasAlt ? altText.substring(0, 60) : '**MISSING**',
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            broken: isBroken,
                        });
                    }
                });

                // Form inputs missing labels
                let unlabeledInputs = 0;
                document.querySelectorAll('input, textarea, select').forEach(input => {
                    if (input.type === 'hidden' || input.type === 'submit') return;
                    const hasLabel = input.labels && input.labels.length > 0;
                    const hasAriaLabel = input.getAttribute('aria-label') || input.getAttribute('aria-labelledby');
                    const hasPlaceholder = input.placeholder;
                    if (!hasLabel && !hasAriaLabel && !hasPlaceholder) {
                        unlabeledInputs++;
                    }
                });

                // Buttons without accessible names
                let unlabeledButtons = 0;
                document.querySelectorAll('button, [role="button"]').forEach(btn => {
                    const text = btn.innerText.trim();
                    const ariaLabel = btn.getAttribute('aria-label');
                    const title = btn.title;
                    if (!text && !ariaLabel && !title) {
                        unlabeledButtons++;
                    }
                });

                // Landmarks
                result.has_main_landmark = !!document.querySelector('main, [role="main"]');
                result.has_nav_landmark = !!document.querySelector('nav, [role="navigation"]');

                // Build accessibility issues list
                if (missingAlt > 0)
                    result.accessibility_issues.push(missingAlt + ' image(s) missing alt attribute (WCAG 1.1.1)');
                if (emptyAlt > 0)
                    result.accessibility_issues.push(emptyAlt + ' image(s) have empty alt="" (verify decorative)');
                if (brokenImages > 0)
                    result.accessibility_issues.push(brokenImages + ' broken/unloaded image(s)');
                if (unlabeledInputs > 0)
                    result.accessibility_issues.push(unlabeledInputs + ' form input(s) missing label/aria-label');
                if (unlabeledButtons > 0)
                    result.accessibility_issues.push(unlabeledButtons + ' button(s) without accessible name');
                if (!result.has_main_landmark)
                    result.accessibility_issues.push('No <main> landmark found');
                if (result.lang === 'not set')
                    result.accessibility_issues.push('Missing lang attribute on <html>');

                // Check for multiple h1s
                const h1Count = document.querySelectorAll('h1').length;
                if (h1Count === 0)
                    result.accessibility_issues.push('No <h1> heading found');
                else if (h1Count > 1)
                    result.accessibility_issues.push(h1Count + ' <h1> headings found (should be 1)');

                // ---- LAYOUT BUGS ----
                result.layout_issues = [];

                // Check for elements overflowing viewport
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                let overflowCount = 0;
                document.querySelectorAll('div, section, article, img, table').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.right > vw + 5) {
                        overflowCount++;
                    }
                });
                if (overflowCount > 0)
                    result.layout_issues.push(overflowCount + ' element(s) overflow the viewport width (causes horizontal scroll)');

                // Check for overlapping clickable elements
                const clickables = document.querySelectorAll('a, button, [role="button"], input[type="submit"]');
                const rects = [];
                clickables.forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        rects.push({ el: el.tagName + (el.innerText || '').trim().substring(0, 20), rect: r });
                    }
                });
                let overlapCount = 0;
                for (let i = 0; i < Math.min(rects.length, 30); i++) {
                    for (let j = i + 1; j < Math.min(rects.length, 30); j++) {
                        const a = rects[i].rect, b = rects[j].rect;
                        const overlapX = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                        const overlapY = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                        const overlapArea = overlapX * overlapY;
                        const minArea = Math.min(a.width * a.height, b.width * b.height);
                        if (overlapArea > minArea * 0.3 && minArea > 100) {
                            overlapCount++;
                            if (overlapCount <= 3) {
                                result.layout_issues.push('Overlapping clickable elements: "' + rects[i].el + '" and "' + rects[j].el + '"');
                            }
                        }
                    }
                }
                if (overlapCount > 3)
                    result.layout_issues.push('...and ' + (overlapCount - 3) + ' more overlapping pairs');

                // ---- BROKEN/DEAD LINKS ----
                result.broken_links = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const text = a.innerText.trim().substring(0, 40);
                    if (href === '#' || href === '' || href === 'javascript:void(0)' || href === 'javascript:;') {
                        if (text && result.broken_links.length < 8) {
                            result.broken_links.push('"' + text + '" has dead href="' + href + '"');
                        }
                    }
                });

                // ---- TINY TEXT ----
                result.tiny_text = [];
                document.querySelectorAll('p, span, li, td, th, label, a').forEach(el => {
                    const style = window.getComputedStyle(el);
                    const fontSize = parseFloat(style.fontSize);
                    const text = el.innerText.trim();
                    if (fontSize < 11 && text.length > 5 && el.offsetParent !== null) {
                        if (result.tiny_text.length < 5) {
                            result.tiny_text.push('"' + text.substring(0, 30) + '..." at ' + fontSize + 'px');
                        }
                    }
                });

                // ---- SECURITY ISSUES ----
                result.security_issues = [];

                // Insecure form actions
                if (window.location.protocol === 'https:') {
                    document.querySelectorAll('form[action]').forEach(form => {
                        const action = form.getAttribute('action') || '';
                        if (action.startsWith('http://')) {
                            result.security_issues.push('Form submits to insecure HTTP: ' + action.substring(0, 60));
                        }
                    });
                }

                // Password fields with autocomplete
                document.querySelectorAll('input[type="password"]').forEach(input => {
                    const ac = input.getAttribute('autocomplete');
                    if (!ac || ac === 'on') {
                        result.security_issues.push('Password field allows autocomplete (should be "new-password" or "off")');
                    }
                });

                // Sensitive data in hidden fields
                document.querySelectorAll('input[type="hidden"]').forEach(input => {
                    const name = (input.name || '').toLowerCase();
                    const value = (input.value || '').toLowerCase();
                    if (name.includes('token') || name.includes('secret') || name.includes('api_key')) {
                        result.security_issues.push('Hidden field "' + name + '" may contain sensitive data');
                    }
                });

                // ---- DUPLICATE IDs ----
                result.duplicate_ids = [];
                const idMap = {};
                document.querySelectorAll('[id]').forEach(el => {
                    const id = el.id;
                    if (id) {
                        idMap[id] = (idMap[id] || 0) + 1;
                    }
                });
                for (const [id, count] of Object.entries(idMap)) {
                    if (count > 1 && result.duplicate_ids.length < 5) {
                        result.duplicate_ids.push('id="' + id + '" appears ' + count + ' times');
                    }
                }

                // Visible text snippet (first 200 chars of body)
                const bodyText = document.body ? document.body.innerText.trim() : '';
                result.visible_text_snippet = bodyText.substring(0, 200);

                return result;
            }""")
            
            # Merge console errors from our listener
            context["console_errors"] = [
                {"type": e["type"], "text": e["text"][:150]}
                for e in self._console_errors[-5:]  # Last 5 errors
            ]
            
            # Merge network errors
            context["network_errors"] = [
                {"url": e["url"][:100], "failure": str(e.get("failure", ""))[:80]}
                for e in self._network_errors[-5:]
            ]
            
            return context
            
        except Exception as e:
            return {
                "title": "",
                "headings": [],
                "images": [],
                "accessibility_issues": [],
                "layout_issues": [],
                "broken_links": [],
                "tiny_text": [],
                "security_issues": [],
                "duplicate_ids": [],
                "console_errors": [],
                "network_errors": [],
            }
    
    def get_console_errors(self) -> List[Dict[str, Any]]:
        """Get captured console errors."""
        return self._console_errors.copy()
    
    def get_network_errors(self) -> List[Dict[str, Any]]:
        """Get captured network errors."""
        return self._network_errors.copy()
    
    def clear_errors(self) -> None:
        """Clear captured errors."""
        self._console_errors.clear()
        self._network_errors.clear()
    
    async def _wait_for_stable(self, timeout: int = 2000) -> None:
        """Wait for the page to be stable after an action."""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            # Timeout is okay, page might not have network activity
            pass
    
    def _handle_console(self, msg) -> None:
        """Handle console messages."""
        if msg.type in ("error", "warning"):
            self._console_errors.append({
                "type": msg.type,
                "text": msg.text,
                "url": self._page.url if self._page else "",
                "timestamp": datetime.now().isoformat(),
            })
    
    def _handle_page_error(self, error) -> None:
        """Handle page errors (uncaught exceptions)."""
        self._console_errors.append({
            "type": "exception",
            "text": str(error),
            "url": self._page.url if self._page else "",
            "timestamp": datetime.now().isoformat(),
        })
    
    def _handle_request_failed(self, request) -> None:
        """Handle failed network requests."""
        self._network_errors.append({
            "url": request.url,
            "failure": request.failure,
            "resource_type": request.resource_type,
            "timestamp": datetime.now().isoformat(),
        })
    
    @property
    def page(self) -> Page:
        """Get the current page object for advanced operations."""
        return self._page
