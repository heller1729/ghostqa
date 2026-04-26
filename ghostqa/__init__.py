"""
GhostQA - Autonomous AI Web Application Testing Agent

An AI-powered testing agent that autonomously explores web applications
to discover bugs and edge cases that traditional scripted tests miss.
"""

__version__ = "0.1.0"
__author__ = "GhostQA Team"

from ghostqa.agent import GhostQAAgent
from ghostqa.config import GhostQAConfig

__all__ = ["GhostQAAgent", "GhostQAConfig", "__version__"]
