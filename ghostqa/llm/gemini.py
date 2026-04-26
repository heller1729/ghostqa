"""
Gemini LLM Provider

Uses the google-genai SDK to interact with Gemini models.
Includes retry logic for transient API errors (503, 429).
"""

import asyncio
import base64
from typing import List

from google import genai
from google.genai import types

from ghostqa.llm.base import LLMProvider, LLMResponse, Message


class GeminiProvider(LLMProvider):
    """Google Gemini provider using the google-genai SDK."""

    MAX_RETRIES = 3
    RETRY_DELAYS = [2, 5, 10]  # seconds

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash"):
        self._api_key = api_key
        self._model = model
        self._client = genai.Client(api_key=api_key)

    async def chat(
        self,
        messages: List[Message],
        json_mode: bool = False,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        system_instruction, contents = self._build_contents(messages)

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction
        if json_mode:
            config.response_mime_type = "application/json"

        response = await self._call_with_retry(contents, config)
        return self._parse_response(response)

    async def chat_with_image(
        self,
        messages: List[Message],
        image_base64: str,
        json_mode: bool = False,
        max_tokens: int = 1500,
        image_mime_type: str = "image/png",
    ) -> LLMResponse:
        system_instruction, contents = self._build_contents(messages)

        # Append the image as a Part to the last user message content
        image_bytes = base64.b64decode(image_base64)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type)

        # Build the final contents: text parts + image part
        if contents:
            last_content = contents[-1]
            if isinstance(last_content, str):
                contents[-1] = types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=last_content),
                        image_part,
                    ],
                )
            elif isinstance(last_content, types.Content):
                last_content.parts.append(image_part)
            else:
                contents.append(image_part)
        else:
            contents = [image_part]

        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
        )
        if system_instruction:
            config.system_instruction = system_instruction
        if json_mode:
            config.response_mime_type = "application/json"

        response = await self._call_with_retry(contents, config)
        return self._parse_response(response)

    async def _call_with_retry(self, contents, config):
        """Call Gemini API with retry logic for transient errors (503, 429)."""
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                error_str = str(e)
                # Only retry on transient errors
                if "503" in error_str or "429" in error_str or "UNAVAILABLE" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    last_error = e
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise  # Non-retryable error, raise immediately
        raise last_error  # All retries exhausted

    def _build_contents(self, messages: List[Message]):
        """Convert Message list to Gemini format (system_instruction + contents)."""
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )
            elif msg.role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )

        return system_instruction, contents

    def _parse_response(self, response) -> LLMResponse:
        """Parse Gemini response into standardized LLMResponse."""
        content = response.text or ""

        input_tokens = 0
        output_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

        return LLMResponse(
            content=content,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_response=response,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._model
