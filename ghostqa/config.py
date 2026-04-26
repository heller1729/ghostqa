"""
GhostQA Configuration

Pydantic models for configuration and settings.
"""

from pydantic import BaseModel, Field
from typing import Optional
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class GhostQAConfig(BaseModel):
    """Configuration for a GhostQA scan session."""

    # Target configuration
    url: str = Field(..., description="URL of the web application to test")
    context: Optional[str] = Field(None, description="Description of the application")

    # Authentication
    username: Optional[str] = Field(None, description="Username for authentication")
    password: Optional[str] = Field(None, description="Password for authentication")

    # Exploration settings
    max_steps: int = Field(50, ge=1, le=500, description="Maximum exploration steps")
    max_depth: int = Field(5, ge=1, le=20, description="Maximum navigation depth")

    # Browser settings
    headless: bool = Field(True, description="Run browser in headless mode")
    viewport_width: int = Field(1920, description="Browser viewport width")
    viewport_height: int = Field(1080, description="Browser viewport height")
    timeout: int = Field(30000, description="Page load timeout in milliseconds")

    # LLM provider settings
    llm_provider: str = Field("google", description="LLM provider: google, openai, anthropic")
    model: Optional[str] = Field(None, description="Model name override (uses provider default if None)")

    # API keys (loaded from environment)
    gemini_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY"),
        description="Google Gemini API key"
    )
    openai_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY"),
        description="OpenAI API key"
    )
    anthropic_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"),
        description="Anthropic API key"
    )

    # Output settings
    output_dir: Optional[str] = Field(None, description="Output directory for reports")
    debug: bool = Field(False, description="Enable debug logging")
    fresh_context: bool = Field(False, description="Ignore saved context, start fresh scan")
    turbo: bool = Field(False, description="Visual agent mode — 1 unified LLM call per step, 2-3× faster")
    record_video: bool = Field(False, description="Record browser session as video")

    # Test strategies
    test_forms: bool = Field(True, description="Test form validation")
    test_navigation: bool = Field(True, description="Test navigation flows")
    test_edge_cases: bool = Field(True, description="Test edge cases and boundaries")
    check_console_errors: bool = Field(True, description="Check for console errors")
    check_visual_issues: bool = Field(True, description="Check for visual anomalies")

    class Config:
        extra = "forbid"

    def get_output_path(self) -> Path:
        """Get the output directory path, creating it if needed."""
        if self.output_dir:
            path = Path(self.output_dir)
        else:
            path = Path("reports")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_api_key(self) -> str:
        """Get the API key for the configured provider."""
        keys = {
            "google": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
        }
        key = keys.get(self.llm_provider)
        if not key:
            raise ValueError(
                f"No API key found for provider '{self.llm_provider}'. "
                f"Set the appropriate environment variable in .env"
            )
        return key


class ScanReport(BaseModel):
    """Report from a completed scan."""

    url: str
    pages_visited: int
    bugs_found: int
    report_path: str
    duration_seconds: float
    steps_taken: int
