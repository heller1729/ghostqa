"""
Persistent Scan Context

Maintains a context file per target URL so the agent can learn
from previous scans and avoid repeating work.
"""

import base64
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse


class ScanContext:
    """Manages persistent context for a target URL across scans."""

    def __init__(self, context_dir: Path):
        self.context_dir = context_dir
        self.context_dir.mkdir(parents=True, exist_ok=True)

        # Context data
        self.target_url: str = ""
        self.last_updated: str = ""
        self.app_fingerprint: str = ""  # Page title to identify the web app uniquely
        self.pages: Dict[str, Dict[str, Any]] = {}
        # pages = { "/#/login": { "description": "...", "elements_explored": [...], "elements_total": [...] } }
        self.known_bugs: List[Dict[str, str]] = []
        self.forms: List[Dict[str, Any]] = []
        self.security_tests: List[Dict[str, str]] = []
        self.observations: List[str] = []
        self.credentials: List[Dict[str, str]] = []
        # credentials = [{ "email": "...", "password": "...", "status": "working|failed|untested", "obtained_via": "..." }]

    @staticmethod
    def url_to_context_name(url: str) -> str:
        """Convert a URL to a safe filename stem."""
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        port = f"_{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
        name = f"{host}{port}"
        # Replace non-alphanumeric chars with underscore
        name = re.sub(r"[^a-zA-Z0-9]", "_", name)
        return name.strip("_")

    def _context_path(self, url: str, app_name: str = "") -> Path:
        base = self.url_to_context_name(url)
        if app_name:
            safe_name = re.sub(r"[^a-zA-Z0-9]", "_", app_name.lower()).strip("_")[:40]
            return self.context_dir / f"{base}__{safe_name}.md"
        return self.context_dir / f"{base}.md"

    def _screenshots_dir(self, url: str) -> Path:
        d = self.context_dir / self.url_to_context_name(url)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def has_context(self, url: str) -> bool:
        """Check if a context file exists for this URL."""
        return self._context_path(url).exists()

    def load(self, url: str) -> bool:
        """Load saved context for a URL. Searches all matching context files."""
        path = self.find_matching_context(url)
        if not path:
            self.target_url = url
            return False
        self.target_url = url
        content = path.read_text(encoding="utf-8")
        self._parse_markdown(content)
        return True

    def find_matching_context(self, url: str) -> Optional[Path]:
        """Find context file for a URL (with or without app name suffix)."""
        self.context_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.url_to_context_name(url)
        candidates = sorted(
            self.context_dir.glob(f"{prefix}*.md"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        return candidates[0] if candidates else None

    def verify_fingerprint(self, current_title: str) -> bool:
        """Check if the loaded context matches the current web app.
        Returns True if fingerprint matches or no fingerprint stored."""
        if not self.app_fingerprint:
            return True  # No fingerprint stored, assume match
        # Fuzzy match — check if the core app name is present
        stored = self.app_fingerprint.lower().strip()
        current = current_title.lower().strip()
        if not current:
            return True  # Can't verify, assume match
        # Match if titles are similar (contain each other's first word)
        stored_first = stored.split()[0] if stored else ""
        current_first = current.split()[0] if current else ""
        return stored_first == current_first or stored in current or current in stored

    def save(self, url: str, app_fingerprint: str = "") -> Path:
        """Save context to file. Filename includes app name for isolation."""
        self.target_url = url
        self.last_updated = datetime.now().isoformat()
        if app_fingerprint:
            self.app_fingerprint = app_fingerprint
        path = self._context_path(url, app_name=self.app_fingerprint)
        path.write_text(self._generate_markdown(), encoding="utf-8")
        return path

    def save_page_screenshot(self, url: str, page_path: str, screenshot_base64: str) -> Path:
        """Save a screenshot for a specific page."""
        screenshots_dir = self._screenshots_dir(url)
        # Convert page path to safe filename
        page_name = re.sub(r"[^a-zA-Z0-9]", "_", page_path).strip("_") or "home"
        img_path = screenshots_dir / f"{page_name}.png"
        img_data = base64.b64decode(screenshot_base64)
        img_path.write_bytes(img_data)
        return img_path

    def get_page_screenshot(self, url: str, page_path: str) -> Optional[str]:
        """Load a previous screenshot as base64, if it exists."""
        screenshots_dir = self._screenshots_dir(url)
        page_name = re.sub(r"[^a-zA-Z0-9]", "_", page_path).strip("_") or "home"
        img_path = screenshots_dir / f"{page_name}.png"
        if img_path.exists():
            return base64.b64encode(img_path.read_bytes()).decode("utf-8")
        return None

    def update_from_scan(
        self,
        pages_visited: set,
        bugs: List[Any],
        discovered_forms: List[Dict[str, Any]],
        explored_elements: List[str],
        security_tests: List[Dict[str, str]] = None,
        observations: List[str] = None,
    ) -> None:
        """Update context with results from a completed scan."""
        # Merge pages
        for page_url in pages_visited:
            path = urlparse(page_url).fragment or urlparse(page_url).path or "/"
            page_key = f"/#{path}" if not path.startswith("/#") else path
            if page_key not in self.pages:
                self.pages[page_key] = {
                    "description": "",
                    "elements_explored": [],
                    "elements_total": [],
                }
            # Merge explored elements for this page
            existing = set(self.pages[page_key].get("elements_explored", []))
            existing.update(e for e in explored_elements if page_key in e or "/" in e)
            self.pages[page_key]["elements_explored"] = list(existing)

        # Merge bugs (avoid duplicates by description)
        existing_descs = {b.get("description", "").lower() for b in self.known_bugs}
        for bug in bugs:
            bug_dict = bug.to_dict() if hasattr(bug, "to_dict") else bug
            desc = bug_dict.get("description", "").lower()
            if desc not in existing_descs:
                self.known_bugs.append({
                    "severity": bug_dict.get("severity", "medium"),
                    "description": bug_dict.get("description", ""),
                    "url": bug_dict.get("url", ""),
                    "type": bug_dict.get("type", "visual"),
                })
                existing_descs.add(desc)

        # Merge forms
        existing_forms = {
            (f.get("url", ""), tuple(f.get("fields", [])))
            for f in self.forms
        }
        for form in discovered_forms:
            key = (form.get("url", ""), tuple(form.get("fields", [])))
            if key not in existing_forms:
                self.forms.append(form)
                existing_forms.add(key)

        # Merge security tests
        if security_tests:
            existing_tests = {t.get("description", "").lower() for t in self.security_tests}
            for test in security_tests:
                if test.get("description", "").lower() not in existing_tests:
                    self.security_tests.append(test)

        # Merge observations
        if observations:
            existing_obs = {o.lower() for o in self.observations}
            for obs in observations:
                if obs.lower() not in existing_obs:
                    self.observations.append(obs)

    def get_unexplored_pages(self, all_available_pages: List[str]) -> List[str]:
        """Return pages that haven't been explored yet."""
        explored = set(self.pages.keys())
        return [p for p in all_available_pages if p not in explored]

    def is_page_fully_explored(self, page_path: str) -> bool:
        """Check if all elements on a page have been explored."""
        page = self.pages.get(page_path, {})
        total = set(page.get("elements_total", []))
        explored = set(page.get("elements_explored", []))
        if not total:
            return False  # Unknown total = not fully explored
        return explored >= total

    def add_credential(self, email: str, password: str, status: str = "working", obtained_via: str = "unknown") -> None:
        """Save a credential from registration or successful login."""
        # Update existing or add new
        for cred in self.credentials:
            if cred["email"] == email:
                cred["password"] = password
                cred["status"] = status
                cred["obtained_via"] = obtained_via
                return
        self.credentials.append({"email": email, "password": password, "status": status, "obtained_via": obtained_via})

    def get_working_credential(self) -> Optional[Dict[str, str]]:
        """Return the first working credential, if any."""
        for cred in self.credentials:
            if cred.get("status") in ("working", "untested"):
                return cred
        return None

    def mark_credential_failed(self, email: str) -> None:
        """Mark a credential as failed (login didn't work)."""
        for cred in self.credentials:
            if cred["email"] == email:
                cred["status"] = "failed"
                return

    def get_context_summary(self) -> str:
        """Generate a text summary for the reasoning prompt."""
        if not self.pages and not self.known_bugs:
            return ""

        lines = ["PREVIOUS SCAN CONTEXT:"]

        if self.pages:
            lines.append(f"\nPages explored ({len(self.pages)}):")
            for path, info in self.pages.items():
                explored_count = len(info.get("elements_explored", []))
                total_count = len(info.get("elements_total", []))
                status = f"({explored_count}/{total_count} elements)" if total_count else "(partial)"
                lines.append(f"  - {path} {status}")

        if self.known_bugs:
            # Show only top 5 bugs to avoid tunnel vision
            priority_bugs = [b for b in self.known_bugs if b.get("severity") in ("critical", "high")]
            other_bugs = [b for b in self.known_bugs if b.get("severity") not in ("critical", "high")]
            top_bugs = (priority_bugs + other_bugs)[:5]
            lines.append(f"\nKnown bugs ({len(self.known_bugs)} total, showing top {len(top_bugs)}):")
            for bug in top_bugs:
                lines.append(f"  - [{bug.get('severity')}] {bug.get('description', '')[:80]}")

        if self.forms:
            lines.append(f"\nForms found ({len(self.forms)}):")
            for form in self.forms:
                fields = ", ".join(form.get("fields", [])[:5])
                lines.append(f"  - {form.get('url', '?')}: {fields}")

        if self.security_tests:
            lines.append(f"\nSecurity tests completed ({len(self.security_tests)}):")
            for test in self.security_tests:
                lines.append(f"  - {test.get('description', '')[:100]}")

        lines.append("""
CONTEXT RULES:
- Your PRIMARY GOAL is to find NEW bugs and explore NEW areas — not re-verify old findings.
- Pages with unexplored elements SHOULD be revisited to complete coverage.
- Do NOT re-report bugs already listed above.
- Do NOT repeat security tests already completed.
- If you choose to REPEAT an action from context, explain WHY in "thinking".
- Spend most of your time on UNEXPLORED pages and features.""")

        if self.credentials:
            working = [c for c in self.credentials if c.get("status") in ("working", "untested")]
            failed = [c for c in self.credentials if c.get("status") == "failed"]
            lines.append(f"\nSaved credentials ({len(self.credentials)}):")
            for cred in working:
                lines.append(f"  - ✅ {cred['email']} / {cred['password']} (via: {cred.get('obtained_via', 'unknown')}, status: {cred['status']})")
            for cred in failed:
                lines.append(f"  - ❌ {cred['email']} (via: {cred.get('obtained_via', 'unknown')}, FAILED)")
            lines.append("\nCREDENTIAL RULES (in priority order):")
            lines.append("1. FIRST try exploitation attacks to log in (SQL injection: ' OR 1=1 --, default admin creds, etc.)")
            lines.append("2. If exploitation fails, use saved ✅ working credentials.")
            lines.append("3. If saved credentials also fail, register a NEW account as last resort.")
            lines.append("- Do NOT re-register with an email already listed above.")

        return "\n".join(lines)

    # --- Markdown parsing/generation ---

    def _generate_markdown(self) -> str:
        """Generate the context .md file content."""
        lines = [
            f"# Scan Context: {self.target_url}",
            f"Last updated: {self.last_updated}",
            f"App: {self.app_fingerprint}" if self.app_fingerprint else "",
            "",
        ]

        # Pages
        lines.append("## Pages Explored")
        for path, info in self.pages.items():
            desc = info.get("description", "")
            explored = len(info.get("elements_explored", []))
            total = len(info.get("elements_total", []))
            status = f"({explored}/{total} elements)" if total else ""
            lines.append(f"- {path} {status} — {desc}" if desc else f"- {path} {status}")
        lines.append("")

        # Bugs
        lines.append("## Known Bugs")
        for bug in self.known_bugs:
            lines.append(f"- [{bug.get('severity', 'medium')}] {bug.get('description', '')} ({bug.get('url', '')})")
        lines.append("")

        # Forms
        lines.append("## Forms Discovered")
        for form in self.forms:
            fields = ", ".join(form.get("fields", []))
            lines.append(f"- {form.get('url', '?')}: {fields}")
        lines.append("")

        # Security tests
        lines.append("## Security Tests Completed")
        for test in self.security_tests:
            lines.append(f"- {test.get('description', '')}")
        lines.append("")

        # Observations
        lines.append("## Observations")
        for obs in self.observations:
            lines.append(f"- {obs}")
        lines.append("")

        # Credentials
        lines.append("## Saved Credentials")
        for cred in self.credentials:
            lines.append(f"- {cred.get('email', '')} | {cred.get('password', '')} | {cred.get('status', 'untested')} | {cred.get('obtained_via', 'unknown')}")
        lines.append("")

        # Screenshots
        ctx_name = self.url_to_context_name(self.target_url)
        screenshots_dir = self.context_dir / ctx_name
        if screenshots_dir.exists():
            lines.append("## Page Screenshots")
            for img in sorted(screenshots_dir.glob("*.png")):
                lines.append(f"- [{img.stem}]({ctx_name}/{img.name})")
            lines.append("")

        return "\n".join(lines)

    def _parse_markdown(self, content: str) -> None:
        """Parse a context .md file back into structured data."""
        current_section = None
        for line in content.split("\n"):
            line = line.strip()

            # Section headers
            if line.startswith("# Scan Context:"):
                self.target_url = line.replace("# Scan Context:", "").strip()
            elif line.startswith("Last updated:"):
                self.last_updated = line.replace("Last updated:", "").strip()
            elif line.startswith("App:"):
                self.app_fingerprint = line.replace("App:", "").strip()
            elif line == "## Pages Explored":
                current_section = "pages"
            elif line == "## Known Bugs":
                current_section = "bugs"
            elif line == "## Forms Discovered":
                current_section = "forms"
            elif line == "## Security Tests Completed":
                current_section = "security"
            elif line == "## Observations":
                current_section = "observations"
            elif line == "## Page Screenshots":
                current_section = "screenshots"
            elif line == "## Saved Credentials":
                current_section = "credentials"
            elif line.startswith("## "):
                current_section = None
            elif line.startswith("- ") and current_section:
                item = line[2:]
                if current_section == "pages":
                    # Parse: "/#/login (5/8 elements) — description"
                    match = re.match(r"^(\S+)\s*(?:\((\d+)/(\d+) elements\))?\s*(?:—\s*(.*))?$", item)
                    if match:
                        path = match.group(1)
                        self.pages[path] = {
                            "description": match.group(4) or "",
                            "elements_explored": ["_"] * int(match.group(2) or 0),
                            "elements_total": ["_"] * int(match.group(3) or 0),
                        }
                elif current_section == "bugs":
                    match = re.match(r"^\[(\w+)\]\s*(.*?)(?:\s*\((https?://.*)\))?$", item)
                    if match:
                        self.known_bugs.append({
                            "severity": match.group(1),
                            "description": match.group(2),
                            "url": match.group(3) or "",
                        })
                elif current_section == "forms":
                    match = re.match(r"^(.*?):\s*(.*)$", item)
                    if match:
                        self.forms.append({
                            "url": match.group(1),
                            "fields": [f.strip() for f in match.group(2).split(",")],
                        })
                elif current_section == "security":
                    self.security_tests.append({"description": item})
                elif current_section == "observations":
                    self.observations.append(item)
                elif current_section == "credentials":
                    parts = [p.strip() for p in item.split("|")]
                    if len(parts) >= 2:
                        self.credentials.append({
                            "email": parts[0],
                            "password": parts[1],
                            "status": parts[2] if len(parts) > 2 else "untested",
                            "obtained_via": parts[3] if len(parts) > 3 else "unknown",
                        })
