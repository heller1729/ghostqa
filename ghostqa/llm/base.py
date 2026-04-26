"""
Base LLM Provider Interface

All providers (Gemini, OpenAI, Claude) implement this interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_response: Any = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Message:
    """A chat message."""
    role: str  # "system", "user", "assistant"
    content: str


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        json_mode: bool = False,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        """
        Send a text-only chat request.

        Args:
            messages: List of Message objects (system, user, assistant)
            json_mode: If True, request JSON-formatted output
            max_tokens: Maximum tokens in the response

        Returns:
            LLMResponse with the model's reply
        """
        ...

    @abstractmethod
    async def chat_with_image(
        self,
        messages: List[Message],
        image_base64: str,
        json_mode: bool = False,
        max_tokens: int = 1500,
        image_mime_type: str = "image/png",
    ) -> LLMResponse:
        """
        Send a chat request with an image.

        Args:
            messages: List of Message objects
            image_base64: Base64-encoded image data
            json_mode: If True, request JSON-formatted output
            max_tokens: Maximum tokens in the response
            image_mime_type: MIME type of the image

        Returns:
            LLMResponse with the model's reply
        """
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this provider (e.g., 'gemini', 'openai', 'claude')."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model being used."""
        ...
