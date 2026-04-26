"""
GhostQA Report Generator

Generates bug reports and exploration summaries.
"""

from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from jinja2 import Environment
import json

from ghostqa.config import GhostQAConfig


class Bug:
    """Represents a detected bug."""
    
    def __init__(
        self,
        bug_type: str,
        severity: str,
        description: str,
        url: str,
        steps: List[str],
        screenshot_before: Optional[str] = None,
        screenshot_after: Optional[str] = None,
        console_errors: Optional[List[Dict]] = None,
        confidence: float = 1.0,
    ):
        self.bug_type = bug_type
        self.severity = severity
        self.description = description
        self.url = url
        self.steps = steps
        self.screenshot_before = screenshot_before
        self.screenshot_after = screenshot_after
        self.console_errors = console_errors or []
        self.confidence = confidence
        self.timestamp = datetime.now().isoformat()
        self.id = f"BUG-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.bug_type,
            "severity": self.severity,
            "description": self.description,
            "url": self.url,
            "steps": self.steps,
            "console_errors": self.console_errors,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }


class Reporter:
    """Generates scan reports."""
    
    HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GhostQA Report - {{ url }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
               line-height: 1.6; color: #333; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                 color: white; padding: 30px; border-radius: 10px; margin-bottom: 20px; }
        h1 { font-size: 2rem; margin-bottom: 10px; }
        .stats { display: flex; gap: 20px; margin-top: 15px; }
        .stat { background: rgba(255,255,255,0.2); padding: 10px 20px; border-radius: 5px; }
        .stat-value { font-size: 1.5rem; font-weight: bold; }
        .stat-label { font-size: 0.9rem; opacity: 0.9; }
        
        .bug-card { background: white; border-radius: 10px; padding: 20px; 
                    margin-bottom: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        .bug-header { display: flex; justify-content: space-between; align-items: center; 
                      margin-bottom: 15px; }
        .bug-title { font-size: 1.1rem; font-weight: 600; }
        .severity { padding: 5px 12px; border-radius: 20px; font-size: 0.8rem; 
                    font-weight: 600; text-transform: uppercase; }
        .severity-critical { background: #fee2e2; color: #dc2626; }
        .severity-high { background: #ffedd5; color: #ea580c; }
        .severity-medium { background: #fef3c7; color: #d97706; }
        .severity-low { background: #dbeafe; color: #2563eb; }
        
        .bug-meta { color: #666; font-size: 0.9rem; margin-bottom: 10px; }
        .bug-description { margin-bottom: 15px; }
        .steps { background: #f8fafc; padding: 15px; border-radius: 5px; }
        .steps h4 { margin-bottom: 10px; font-size: 0.9rem; color: #666; }
        .steps ol { margin-left: 20px; }
        .steps li { margin-bottom: 5px; }
        
        .console-errors { margin-top: 15px; }
        .console-error { background: #fef2f2; border-left: 3px solid #dc2626; 
                         padding: 10px; margin-bottom: 5px; font-family: monospace; 
                         font-size: 0.85rem; overflow-x: auto; }
        
        .no-bugs { text-align: center; padding: 50px; color: #666; }
        .no-bugs-icon { font-size: 4rem; margin-bottom: 15px; }
        
        .footer { text-align: center; color: #666; margin-top: 30px; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>🔍 GhostQA Report</h1>
            <p>{{ url }}</p>
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{{ bugs | length }}</div>
                    <div class="stat-label">Bugs Found</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ pages_visited }}</div>
                    <div class="stat-label">Pages Visited</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ steps_taken }}</div>
                    <div class="stat-label">Steps Taken</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ duration }}</div>
                    <div class="stat-label">Duration</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{{ model }}</div>
                    <div class="stat-label">Model</div>
                </div>
            </div>
        </header>
        
        {% if bugs %}
        <h2 style="margin-bottom: 15px;">🐛 Bugs Found</h2>
        {% for bug in bugs %}
        <div class="bug-card">
            <div class="bug-header">
                <span class="bug-title">{{ bug.id }}: {{ bug.description[:80] }}{% if bug.description|length > 80 %}...{% endif %}</span>
                <span class="severity severity-{{ bug.severity }}">{{ bug.severity }}</span>
            </div>
            <div class="bug-meta">
                <strong>Type:</strong> {{ bug.type }} | 
                <strong>URL:</strong> {{ bug.url }} |
                <strong>Confidence:</strong> {{ (bug.confidence * 100)|round }}%
            </div>
            <div class="bug-description">{{ bug.description }}</div>
            <div class="steps">
                <h4>Reproduction Steps:</h4>
                <ol>
                {% for step in bug.steps %}
                    <li>{{ step }}</li>
                {% endfor %}
                </ol>
            </div>
            {% if bug.console_errors %}
            <div class="console-errors">
                <h4>Console Errors:</h4>
                {% for error in bug.console_errors %}
                <div class="console-error">{{ error.text }}</div>
                {% endfor %}
            </div>
            {% endif %}
        </div>
        {% endfor %}
        {% else %}
        <div class="bug-card no-bugs">
            <div class="no-bugs-icon">✅</div>
            <h3>No Bugs Found</h3>
            <p>The scan completed without detecting any issues.</p>
        </div>
        {% endif %}
        
        <div class="footer">
            Generated by GhostQA on {{ generated_at }}
        </div>
    </div>
</body>
</html>
"""
    
    def __init__(self, config: GhostQAConfig):
        self.config = config
        self.bugs: List[Bug] = []
        self.pages_visited: set = set()
        self.steps_taken: int = 0
        self.start_time: Optional[datetime] = None
        self.context_used: bool = False  # Set by agent if context file was loaded
        self.user_context: str = config.context or ""  # User-provided focus instructions
        self.scan_category: str = self._detect_category(self.user_context)  # Auto-detected
        # Resolve model name
        from ghostqa.llm.factory import DEFAULT_MODELS
        self.model_name = config.model or DEFAULT_MODELS.get(config.llm_provider.lower(), "unknown")
        self._video_path: Optional[str] = None

    @staticmethod
    def _detect_category(context: str) -> str:
        """Auto-detect scan category from user-provided context."""
        if not context:
            return "general"
        ctx = context.lower()
        if any(k in ctx for k in ("sql", "xss", "injection", "exploit", "security", "attack", "vulnerab", "auth", "bypass")):
            return "security"
        if any(k in ctx for k in ("ui", "visual", "layout", "design", "css", "responsive", "broken", "buggy")):
            return "ui"
        if any(k in ctx for k in ("edge", "boundary", "stress", "invalid", "negative", "limit")):
            return "edge_cases"
        if any(k in ctx for k in ("accessibility", "a11y", "wcag", "screen reader", "aria")):
            return "accessibility"
        if any(k in ctx for k in ("navigate", "navigation", "flow", "page", "path", "link")):
            return "navigation"
        if any(k in ctx for k in ("performance", "speed", "load", "slow")):
            return "performance"
        return "general"
    
    def start_scan(self) -> None:
        """Mark the start of a scan."""
        self.start_time = datetime.now()
        self.bugs.clear()
        self.pages_visited.clear()
        self.steps_taken = 0
    
    def add_bug(self, bug: Bug) -> None:
        """Add a detected bug, skipping near-duplicates and false positives."""
        # Filter false positives first
        if self._is_false_positive(bug):
            return
        # Enforce correct severity based on keywords
        bug = self._enforce_severity(bug)
        if self._is_duplicate(bug):
            return
        self.bugs.append(bug)

    def _is_duplicate(self, new_bug: Bug) -> bool:
        """Check if a bug is a near-duplicate of an existing one (cross-page)."""
        new_desc = new_bug.description.lower().strip()
        new_normalized = self._normalize_entities(new_desc)
        new_keywords = self._extract_keywords(new_normalized)

        for existing in self.bugs:
            existing_desc = existing.description.lower().strip()

            # Substring match — only require same URL for short descriptions
            # to avoid false cross-page matches on generic 3-word descriptions
            if new_desc in existing_desc or existing_desc in new_desc:
                if len(new_desc) > 40 or existing.url == new_bug.url:
                    return True

            # Normalize entities and compare keywords (cross-page)
            existing_normalized = self._normalize_entities(existing_desc)
            existing_keywords = self._extract_keywords(existing_normalized)
            if new_keywords and existing_keywords:
                overlap = len(new_keywords & existing_keywords) / min(len(new_keywords), len(existing_keywords))
                if overlap >= 0.6:  # Tighter threshold for cross-page
                    return True

            # Word-level overlap (same URL only — too noisy cross-page)
            if existing.url == new_bug.url:
                new_words = set(new_normalized.split())
                existing_words = set(existing_normalized.split())
                if new_words and existing_words:
                    overlap = len(new_words & existing_words) / min(len(new_words), len(existing_words))
                    if overlap > 0.35:
                        return True

        return False

    @staticmethod
    def _normalize_entities(description: str) -> str:
        """Normalize synonym groups so dedup catches semantic duplicates."""
        # Synonym groups: replace any of these words with a canonical form
        synonyms = {
            "cookie_banner": ["cookie", "consent", "banner", "cookie banner", "cookie consent", "cookie popup", "consent pop-up"],
            "sold_out": ["sold out", "sold-out", "soldout", "ribbon", "badge"],
            "modal": ["modal", "dialog", "popup", "pop-up", "overlay", "welcome modal"],
            "currency": ["currency", "¤", "currency symbol", "currency sign", "price symbol"],
            "truncated": ["truncated", "truncation", "ellipsis", "..."],
            "obscure": ["obscure", "obscures", "obscuring", "obstruct", "obstruction", "blocking", "covers", "covering", "overlap", "overlapping", "overlaps", "partially overlapping"],
            "slider": ["slider", "rating slider", "rating widget", "rating", "visual indicator", "green circle", "green dot"],
            "unclear_value": ["not immediately clear", "unclear", "not explicit", "without an explicit", "without explicit"],
            "image_product": ["product image", "product card", "product name", "product title", "product cards"],
            "disabled_button": ["disabled", "greyed out", "grayed out", "greyed", "grayed", "grey out", "gray out", "appears disabled", "button appears"],
            "dropdown_menu": ["dropdown", "drop-down", "dropdown menu", "account dropdown", "login dropdown", "menu is open", "stuck open", "persistently visible"],
            "empty_space": ["empty space", "empty spaces", "large empty", "missing content", "inconsistent card layout"],
            "sanitization": ["sanitization", "sanitize", "sanitized", "injection", "xss", "sql injection", "script tags", "input validation", "no input"],
            "timer_urgency": ["timer", "countdown", "ticking", "hurry up", "time is ticking", "urgency", "artificial urgency", "false urgency", "stress", "anxiety", "pressure", "clock"],
            "dark_pattern": ["dark pattern", "deceptive", "misleading", "confusing label", "negative logic", "double negative", "inverted", "trick", "tricky"],
            "close_button": ["close button", "x button", "close icon", "dismiss", "©lose", "hidden close", "tiny close", "obscured close"],
            "low_contrast": ["low contrast", "poor contrast", "faint", "invisible", "hard to see", "hard to read", "barely visible", "extremely low"],
            "confusing_label": ["confusing", "unintuitive", "non-standard", "unconventional", "counter-intuitive", "counter intuitive", "ambiguous"],
        }
        result = description
        for canonical, variants in synonyms.items():
            # Sort by length descending to replace longer phrases first
            for variant in sorted(variants, key=len, reverse=True):
                if variant in result:
                    result = result.replace(variant, canonical)
        return result

    @staticmethod
    def _extract_keywords(description: str) -> set:
        """Extract topic keywords from a bug description for dedup."""
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "and", "or", "but", "in", "on", "at", "to", "for", "of",
            "with", "by", "from", "that", "this", "which", "it", "its",
            "not", "no", "has", "have", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "can",
            "used", "instead", "like", "specific", "seems", "might",
            "page", "product", "one", "also", "out", "up", "than",
            "part", "parts", "some", "same", "other", "being",
        }
        words = set(description.lower().split())
        return words - stopwords

    @staticmethod
    def _is_false_positive(bug: Bug) -> bool:
        """Filter out observations and self-contradicting bugs."""
        desc = bug.description.lower()
        
        # Observation patterns — not actual bugs
        false_patterns = [
            "this is owasp",
            "this is a deliberately",
            "this is a security test",
            "this is a classic",
            "which may be intentional",
            "is actually correct",
            "stock may be limited",
            "could be intentional",
        ]
        for pattern in false_patterns:
            if pattern in desc:
                return True
        
        # Self-contradictions (e.g., "X is incorrect — X is actually correct")
        if "incorrect" in desc and "actually correct" in desc:
            return True
        
        return False

    @staticmethod
    def _enforce_severity(bug: Bug) -> Bug:
        """Override severity based on bug description keywords."""
        desc = bug.description.lower()
        
        # Critical: security vulnerabilities
        critical_keywords = [
            "xss", "sql injection", "cross-site scripting",
            "script tags", "no input sanitization", "without sanitization",
            "authentication bypass", "data exposed", "stack trace",
            "debug info", "credentials visible",
        ]
        for kw in critical_keywords:
            if kw in desc:
                bug.severity = "critical"
                return bug
        
        # High: functionality broken
        high_keywords = [
            "broken", "fails", "crash", "500 error", "server error",
            "form submission fails", "broken link", "broken image",
            "missing content", "page not found", "404",
        ]
        for kw in high_keywords:
            if kw in desc:
                bug.severity = "high"
                return bug
        
        # Low: cosmetic / minor
        low_keywords = [
            "minor", "cosmetic", "slight", "tooltip",
            "placeholder text", "lorem ipsum",
            "low-resolution", "pixelated",
        ]
        for kw in low_keywords:
            if kw in desc:
                bug.severity = "low"
                return bug
        
        # Keep original severity for everything else
        return bug

    
    def add_page(self, url: str) -> None:
        """Record a visited page."""
        self.pages_visited.add(url.split("?")[0])  # Remove query params
    
    def increment_steps(self) -> None:
        """Increment step counter."""
        self.steps_taken += 1
    
    def get_duration(self) -> str:
        """Get scan duration as formatted string."""
        if not self.start_time:
            return "0s"
        delta = datetime.now() - self.start_time
        minutes = int(delta.total_seconds() // 60)
        seconds = int(delta.total_seconds() % 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    
    def get_duration_seconds(self) -> float:
        """Get scan duration in seconds."""
        if not self.start_time:
            return 0.0
        return (datetime.now() - self.start_time).total_seconds()
    
    def generate_html_report(self) -> str:
        """Generate HTML report with auto-escaped content."""
        env = Environment(autoescape=True)
        template = env.from_string(self.HTML_TEMPLATE)
        return template.render(
            url=self.config.url,
            bugs=[bug.to_dict() for bug in self.bugs],
            pages_visited=len(self.pages_visited),
            steps_taken=self.steps_taken,
            duration=self.get_duration(),
            model=self.model_name,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    
    def generate_json_report(self) -> str:
        """Generate JSON report."""
        return json.dumps({
            "url": self.config.url,
            "model": self.model_name,
            "context_used": self.context_used,
            "user_context": self.user_context or None,
            "scan_category": self.scan_category,
            "turbo_mode": self.config.turbo if hasattr(self.config, 'turbo') else False,
            "video_path": self._video_path,
            "scan_time": datetime.now().isoformat(),
            "duration_seconds": self.get_duration_seconds(),
            "pages_visited": list(self.pages_visited),
            "steps_taken": self.steps_taken,
            "bugs": [bug.to_dict() for bug in self.bugs],
            "summary": {
                "total_bugs": len(self.bugs),
                "critical": sum(1 for b in self.bugs if b.severity == "critical"),
                "high": sum(1 for b in self.bugs if b.severity == "high"),
                "medium": sum(1 for b in self.bugs if b.severity == "medium"),
                "low": sum(1 for b in self.bugs if b.severity == "low"),
            }
        }, indent=2)
    
    def save_reports(self) -> Path:
        """Save both HTML and JSON reports, return path to HTML."""
        output_dir = self.config.get_output_path()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save HTML
        html_path = output_dir / f"report_{timestamp}.html"
        html_path.write_text(self.generate_html_report())
        
        # Save JSON
        json_path = output_dir / f"report_{timestamp}.json"
        json_path.write_text(self.generate_json_report())
        
        return html_path
