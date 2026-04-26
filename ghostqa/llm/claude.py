"""
Claude (Anthropic) LLM Provider

Uses the Anthropic SDK to interact with Claude models.
"""

from typing import List

from anthropic import Anthropic

from ghostqa.llm.base import LLMProvider, LLMResponse, Message


class ClaudeProvider(LLMProvider):
    """Anthropic Claude provider using the official SDK."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._model = model
        self._client = Anthropic(api_key=api_key)

    async def chat(
        self,
        messages: List[Message],
        json_mode: bool = False,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        system_prompt, anthropic_messages = self._build_messages(messages, json_mode)

        kwargs = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = self._client.messages.create(**kwargs)
        return self._parse_response(response)

    async def chat_with_image(
        self,
        messages: List[Message],
        image_base64: str,
        json_mode: bool = False,
        max_tokens: int = 1500,
        image_mime_type: str = "image/png",
    ) -> LLMResponse:
        system_prompt, anthropic_messages = self._build_messages(messages, json_mode)

        # Find the last user message and add the image
        for i in range(len(anthropic_messages) - 1, -1, -1):
            if anthropic_messages[i]["role"] == "user":
                text_content = anthropic_messages[i]["content"]
                anthropic_messages[i]["content"] = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_mime_type,
                            "data": image_base64,
                        },
                    },
                    {"type": "text", "text": text_content},
                ]
                break

        kwargs = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = self._client.messages.create(**kwargs)
        return self._parse_response(response)

    def _build_messages(self, messages: List[Message], json_mode: bool = False):
        """Convert Message list to Anthropic format (separate system prompt)."""
        system_prompt = None
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
                if json_mode:
                    system_prompt += "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no code fences, just raw JSON."
            else:
                anthropic_messages.append({
                    "role": msg.role,
                    "content": msg.content,
                })

        return system_prompt, anthropic_messages

    def _parse_response(self, response) -> LLMResponse:
        """Parse Anthropic response into standardized LLMResponse."""
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return LLMResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
            raw_response=response,
        )

    @property
    def provider_name(self) -> str:
        return "claude"

    @property
    def model_name(self) -> str:
        return self._model
