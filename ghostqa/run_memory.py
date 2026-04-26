"""
GhostQA Run Memory

Maintains the full context of all steps taken during a single scan run.
Uses a two-tier approach:
  Tier 1 (compressed): LLM-generated summary of older steps
  Tier 2 (raw): Last N steps in full detail

This gives the agent both the big picture and precise recent context
without blowing up the token budget.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StepRecord:
    """Record of a single agent step."""
    step_number: int
    url: str
    action_type: str
    target: str = ""
    value: str = ""
    reasoning: str = ""
    success: bool = True
    bugs_found: List[str] = field(default_factory=list)
    page_title: str = ""
    observations: str = ""  # what the agent noticed on this page

    def to_summary_line(self) -> str:
        """Compact single-line representation for the compressed summary."""
        result = "OK" if self.success else "FAIL"
        bugs_str = f" | BUGS: {', '.join(self.bugs_found)}" if self.bugs_found else ""
        obs_str = f" | NOTE: {self.observations}" if self.observations else ""
        return (
            f"Step {self.step_number}: [{result}] {self.action_type} "
            f"'{self.target}' on {self.url}{bugs_str}{obs_str}"
        )

    def to_detailed_block(self) -> str:
        """Detailed multi-line representation for raw recent steps."""
        lines = [f"Step {self.step_number} ({self.url}):"]
        lines.append(f"  Action: {self.action_type} -> {self.target}")
        if self.value:
            # Truncate very long values (like form payloads)
            val_display = self.value[:80] + "..." if len(self.value) > 80 else self.value
            lines.append(f"  Value: {val_display}")
        if self.reasoning:
            lines.append(f"  Reason: {self.reasoning}")
        result = "Success" if self.success else "Failed"
        lines.append(f"  Result: {result}")
        if self.bugs_found:
            for bug in self.bugs_found:
                lines.append(f"  Bug found: {bug}")
        if self.observations:
            lines.append(f"  Observation: {self.observations}")
        return "\n".join(lines)


class RunMemory:
    """
    Maintains the full context of a scan run.

    Two-tier design:
    - compressed_summary: LLM-generated summary of steps 1 through (N - RECENT_WINDOW)
    - recent_steps: last RECENT_WINDOW steps in full detail

    The agent always sees both tiers, giving it the complete picture
    of the entire run without exceeding token limits.
    """

    RECENT_WINDOW = 5          # number of raw recent steps to keep
    COMPRESSION_INTERVAL = 10  # compress every N steps

    def __init__(self):
        self.steps: List[StepRecord] = []
        self.compressed_summary: str = ""
        self.last_compressed_step: int = 0  # step number up to which summary covers
        self.total_bugs_found: int = 0
        self.pages_visited: set = set()
        self.run_start: str = datetime.now().isoformat()

    def record_step(
        self,
        step_number: int,
        url: str,
        action_type: str,
        target: str = "",
        value: str = "",
        reasoning: str = "",
        success: bool = True,
        bugs_found: List[str] = None,
        page_title: str = "",
        observations: str = "",
    ) -> None:
        """Record a completed step."""
        record = StepRecord(
            step_number=step_number,
            url=url,
            action_type=action_type,
            target=target,
            value=value,
            reasoning=reasoning,
            success=success,
            bugs_found=bugs_found or [],
            page_title=page_title,
            observations=observations,
        )
        self.steps.append(record)
        self.total_bugs_found += len(record.bugs_found)
        self.pages_visited.add(url)

    def needs_compression(self) -> bool:
        """Check if we have enough uncompressed steps to warrant compression."""
        uncompressed_count = len(self.steps) - self.last_compressed_step
        # Only compress if we have more than RECENT_WINDOW + COMPRESSION_INTERVAL steps
        return uncompressed_count > (self.RECENT_WINDOW + self.COMPRESSION_INTERVAL)

    async def compress(self, llm_provider) -> None:
        """
        Compress older steps into a narrative summary using the LLM.
        Keeps the last RECENT_WINDOW steps as raw detail.
        """
        if not self.needs_compression():
            return

        # Steps to compress: everything except the last RECENT_WINDOW
        cutoff_index = len(self.steps) - self.RECENT_WINDOW
        steps_to_compress = self.steps[:cutoff_index]

        if not steps_to_compress:
            return

        # Build the raw log of steps to compress
        step_lines = [s.to_summary_line() for s in steps_to_compress]
        raw_log = "\n".join(step_lines)

        # Include existing compressed summary for continuity
        existing = ""
        if self.compressed_summary:
            existing = f"Previous summary (steps 1-{self.last_compressed_step}):\n{self.compressed_summary}\n\nNew steps to integrate:\n"

        from ghostqa.llm.base import Message

        messages = [
            Message(role="system", content=(
                "You are a QA testing assistant. Compress the following step log "
                "into a concise narrative summary that preserves all important context. "
                "Focus on: pages explored, bugs found (with descriptions), forms tested, "
                "security payloads tried, failed actions (and why), and observations. "
                "Use bullet points. Keep it under 800 words. "
                "Do NOT lose any bug descriptions or security findings."
            )),
            Message(role="user", content=f"{existing}{raw_log}"),
        ]

        try:
            response = await llm_provider.chat(
                messages=messages,
                max_tokens=1500,
            )
            self.compressed_summary = response.content.strip()
            self.last_compressed_step = steps_to_compress[-1].step_number
        except Exception:
            # If compression fails, fall back to a simple concatenation
            self.compressed_summary = "\n".join(
                s.to_summary_line() for s in steps_to_compress
            )
            self.last_compressed_step = steps_to_compress[-1].step_number

    def get_full_context(self) -> str:
        """
        Build the full run context string for injection into the LLM prompt.

        Returns a two-section string:
        1. Compressed summary of earlier steps (if any)
        2. Detailed view of the last RECENT_WINDOW steps
        """
        if not self.steps:
            return "No previous actions yet. Start exploring!"

        sections = []

        # Header with run stats
        sections.append(
            f"RUN CONTEXT ({len(self.steps)} steps completed, "
            f"{len(self.pages_visited)} pages visited, "
            f"{self.total_bugs_found} bugs found so far):"
        )

        # Tier 1: Compressed summary of older steps
        if self.compressed_summary:
            sections.append(
                f"\n--- Summary of steps 1-{self.last_compressed_step} ---\n"
                f"{self.compressed_summary}"
            )

        # Tier 2: Raw recent steps
        recent_start = max(0, len(self.steps) - self.RECENT_WINDOW)
        recent = self.steps[recent_start:]

        if recent:
            if self.compressed_summary:
                sections.append(f"\n--- Detailed recent steps ---")
            sections.append("")
            for step in recent:
                sections.append(step.to_detailed_block())

        return "\n".join(sections)

    def get_all_bugs(self) -> List[str]:
        """Get all bugs found during this run."""
        bugs = []
        for step in self.steps:
            bugs.extend(step.bugs_found)
        return bugs

    def get_run_stats(self) -> Dict[str, Any]:
        """Get summary statistics for this run."""
        failed_steps = sum(1 for s in self.steps if not s.success)
        action_types = {}
        for s in self.steps:
            action_types[s.action_type] = action_types.get(s.action_type, 0) + 1

        return {
            "total_steps": len(self.steps),
            "pages_visited": len(self.pages_visited),
            "bugs_found": self.total_bugs_found,
            "failed_steps": failed_steps,
            "action_breakdown": action_types,
            "run_start": self.run_start,
        }
