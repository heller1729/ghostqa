"""
LLM Provider Factory

Creates the appropriate LLM provider based on configuration.
"""

from ghostqa.llm.base import LLMProvider


# Default models per provider
DEFAULT_MODELS = {
    "google": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
}


def create_provider(
    provider: str,
    api_key: str,
    model: str = None,
) -> LLMProvider:
    """
    Create an LLM provider instance.

    Args:
        provider: Provider name ('google', 'openai', 'anthropic')
        api_key: API key for the provider
        model: Optional model override (uses provider default if None)

    Returns:
        LLMProvider instance

    Raises:
        ValueError: If provider is unknown or api_key is missing
    """
    if not api_key:
        raise ValueError(f"API key is required for provider '{provider}'")

    provider = provider.lower().strip()
    model = model or DEFAULT_MODELS.get(provider)

    if provider == "google":
        from ghostqa.llm.gemini import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model)

    elif provider == "openai":
        from ghostqa.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, model=model)

    elif provider == "anthropic":
        from ghostqa.llm.claude import ClaudeProvider
        return ClaudeProvider(api_key=api_key, model=model)

    else:
        raise ValueError(
            f"Unknown provider '{provider}'. Supported: google, openai, anthropic"
        )
