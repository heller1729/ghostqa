"""
OpenAI LLM Provider

Uses the OpenAI SDK to interact with GPT models.
"""

from typing import List

from openai import OpenAI

from ghostqa.llm.base import LLMProvider, LLMResponse, Message


class OpenAIProvider(LLMProvider):
    """OpenAI provider using the official SDK."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self._api_key = api_key
        self._model = model
        self._client = OpenAI(api_key=api_key)

    async def chat(
        self,
        messages: List[Message],
        json_mode: bool = False,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        openai_messages = self._build_messages(messages)

        kwargs = {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    async def chat_with_image(
        self,
        messages: List[Message],
        image_base64: str,
        json_mode: bool = False,
        max_tokens: int = 1500,
        image_mime_type: str = "image/png",
    ) -> LLMResponse:
        openai_messages = self._build_messages(messages)

        # Find the last user message and add the image to it
        for i in range(len(openai_messages) - 1, -1, -1):
            if openai_messages[i]["role"] == "user":
                text_content = openai_messages[i]["content"]
                openai_messages[i]["content"] = [
                    {"type": "text", "text": text_content},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime_type};base64,{image_base64}",
                            "detail": "high",
                        },
                    },
                ]
                break

        kwargs = {
            "model": self._model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        return self._parse_response(response)

    def _build_messages(self, messages: List[Message]) -> list:
        """Convert Message list to OpenAI format."""
        return [{"role": msg.role, "content": msg.content} for msg in messages]

    def _parse_response(self, response) -> LLMResponse:
        """Parse OpenAI response into standardized LLMResponse."""
        choice = response.choices[0]
        usage = response.usage

        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            raw_response=response,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model
