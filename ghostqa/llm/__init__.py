"""
GhostQA LLM Abstraction Layer

Supports multiple LLM providers: Gemini, OpenAI, Claude.
Switch providers via config or CLI --provider flag.
"""

from ghostqa.llm.base import LLMProvider, LLMResponse
from ghostqa.llm.factory import create_provider

__all__ = ["LLMProvider", "LLMResponse", "create_provider"]
