"""
GhostQA Random Explorer Baseline

A minimal agent that navigates randomly and uses only DOM-based
heuristic checks to detect bugs. No LLM calls at all.

This proves that the LLM adds value beyond simple DOM heuristics.
"""

import asyncio
import random
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any

from ghostqa.browser import BrowserController
from ghostqa.reporter import Bug, Reporter
from ghostqa.config import GhostQAConfig


class RandomExplorerBaseline:
    """
    Random exploration baseline agent.
    
    - Navigates to the URL
    - Each step: picks a random link/button from DOM and clicks it
    - Scrolls randomly
    - Uses ONLY DOM-based heuristic checks to detect bugs
    - No LLM calls whatsoever
    """
    
    def __init__(self, config: GhostQAConfig):
        self.config = config
        self.browser = None
        self.bugs: List[Bug] = []
        self.pages_visited = set()
        self.steps_taken = 0
        self.seen_issues = set()  # Dedup by description hash
    
    async def run(self):
        """Run the random explorer baseline."""
        start_time = datetime.now()
        
        self.browser = BrowserController(
            headless=self.config.headless,
            record_video=False,
        )
        
        try:
            await self.browser.launch()
            await self.browser.navigate(self.config.url)
            self.pages_visited.add(self.config.url)
            
            for step in range(self.config.max_steps):
                self.steps_taken = step + 1
                current_url = await self.browser.get_current_url()
                self.pages_visited.add(current_url)
                
                # 1. Run DOM-based heuristic checks
                await self._check_dom_bugs(current_url)
                
                # 2. Take a random action
                await self._random_action()
                
                # Small delay for page to settle
                await asyncio.sleep(1.0)
            
            # Final check on last page
            final_url = await self.browser.get_current_url()
            self.pages_visited.add(final_url)
            await self._check_dom_bugs(final_url)
            
        finally:
            if self.browser:
                await self.browser.close()
        
        # Generate report
        duration = (datetime.now() - start_time).total_seconds()
        return self._generate_report(duration)
    
    async def _check_dom_bugs(self, url: str):
        """Run DOM-based heuristic checks and create bugs from findings."""
        try:
            context = await self.browser.get_page_context()
        except Exception:
            return
        
        # Accessibility issues
        for issue in context.get("accessibility_issues", []):
            self._add_bug("accessibility", "medium", issue, url)
        
        # Layout issues
        for issue in context.get("layout_issues", []):
            self._add_bug("layout", "medium", issue, url)
        
        # Broken links
        for link in context.get("broken_links", []):
            self._add_bug("functional", "medium", f"Broken/dead link: {link}", url)
        
        # Tiny text
        for text in context.get("tiny_text", []):
            self._add_bug("accessibility", "low", f"Tiny text detected: {text}", url)
        
        # Security issues
        for issue in context.get("security_issues", []):
            self._add_bug("security", "high", issue, url)
        
        # Duplicate IDs
        for dup in context.get("duplicate_ids", []):
            self._add_bug("functional", "low", f"Duplicate ID: {dup}", url)
        
        # Console errors
        for err in context.get("console_errors", []):
            self._add_bug("error", "medium", 
                         f"Console error: [{err.get('type', '?')}] {err.get('text', '?')}", url)
        
        # Network errors
        for err in context.get("network_errors", []):
            self._add_bug("error", "medium",
                         f"Network error: {err.get('url', '?')} ({err.get('failure', '?')})", url)
        
        # Broken images
        images = context.get("images", [])
        missing_alt = sum(1 for img in images if img.get("alt") == "**MISSING**")
        broken = sum(1 for img in images if img.get("broken"))
        if missing_alt > 0:
            self._add_bug("accessibility", "medium", 
                         f"{missing_alt} image(s) missing alt text", url)
        if broken > 0:
            self._add_bug("visual", "high",
                         f"{broken} broken/unloaded image(s)", url)
    
    def _add_bug(self, bug_type: str, severity: str, description: str, url: str):
        """Add a bug if not already seen (dedup by description similarity)."""
        # Simple dedup: normalize and hash
        desc_key = description.lower().strip()[:80]
        if desc_key in self.seen_issues:
            return
        self.seen_issues.add(desc_key)
        
        bug = Bug(
            bug_type=bug_type,
            severity=severity,
            description=description,
            url=url,
            steps=[f"Navigate to {url}", "DOM heuristic check (automated)"],
            confidence=0.6,  # Lower confidence since no visual confirmation
        )
        self.bugs.append(bug)
    
    async def _random_action(self):
        """Take a random action: click a random link/button or scroll."""
        action = random.choice(["click_link", "click_button", "scroll", "scroll"])
        
        try:
            if action == "click_link":
                dom = await self.browser.get_dom_elements()
                links = dom.get("links", [])
                if links:
                    link = random.choice(links)
                    href = link.get("href", "")
                    if href and not href.startswith("javascript"):
                        try:
                            await self.browser.navigate(href)
                        except Exception:
                            await self.browser.scroll("down")
                    else:
                        await self.browser.scroll("down")
                else:
                    await self.browser.scroll("down")
                    
            elif action == "click_button":
                dom = await self.browser.get_dom_elements()
                buttons = dom.get("buttons", [])
                if buttons:
                    button = random.choice(buttons)
                    text = button.get("text", "")
                    if text:
                        try:
                            await self.browser.click(f"text={text}")
                        except Exception:
                            await self.browser.scroll("down")
                    else:
                        await self.browser.scroll("down")
                else:
                    await self.browser.scroll("down")
                    
            elif action == "scroll":
                direction = random.choice(["down", "down", "down", "up"])
                await self.browser.scroll(direction)
                
        except Exception:
            # If anything fails, just scroll
            try:
                await self.browser.scroll("down")
            except Exception:
                pass
    
    def _generate_report(self, duration: float) -> Dict[str, Any]:
        """Generate a report in the same format as the main agent."""
        bug_dicts = [b.to_dict() for b in self.bugs]
        
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for b in bug_dicts:
            sev = b.get("severity", "low")
            if sev in severity_counts:
                severity_counts[sev] += 1
        
        report = {
            "url": self.config.url,
            "model": "baseline-random",
            "context_used": False,
            "user_context": "",
            "scan_category": "ui",
            "turbo_mode": False,
            "baseline_mode": True,
            "video_path": None,
            "scan_time": datetime.now().isoformat(),
            "duration_seconds": duration,
            "pages_visited": list(self.pages_visited),
            "steps_taken": self.steps_taken,
            "bugs": bug_dicts,
            "summary": {
                "total_bugs": len(bug_dicts),
                **severity_counts,
            },
        }
        
        # Save report
        output_dir = Path(self.config.output_dir or "reports")
        output_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = output_dir / f"baseline_{timestamp}.json"
        
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        report["report_path"] = str(report_path)
        return report
