"""
tools/llm_provider.py — Pluggable LLM Provider

Supports: Gemini, OpenAI, Anthropic, Ollama (and any OpenAI-compatible endpoint).

Switch providers by setting LLM_PROVIDER in config.py or via environment:
    LLM_PROVIDER=gemini       → Google Gemini (default)
    LLM_PROVIDER=openai       → OpenAI GPT
    LLM_PROVIDER=anthropic    → Anthropic Claude
    LLM_PROVIDER=ollama       → Ollama (local, OpenAI-compatible)
    LLM_PROVIDER=openai_compat → Any OpenAI-compatible API (set LLM_BASE_URL)

All providers expose a single interface:
    provider.generate(system_prompt: str, user_prompt: str) -> (text: str, tokens: int)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


# ─── Abstract Interface ───────────────────────────────────────────────────────

class LLMProvider(ABC):
    """Common interface every provider must implement."""

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> tuple[str, int]:
        """
        Call the LLM and return (response_text, total_token_count).
        token_count may be 0 if the provider doesn't expose it.
        """
        ...


# ─── Gemini Provider ──────────────────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    """Google Gemini via google-generativeai."""

    def __init__(self, model: str, generation_config: dict) -> None:
        import google.generativeai as genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY is not set.")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            generation_config=generation_config,
        )

    def generate(self, system_prompt: str, user_prompt: str) -> tuple[str, int]:
        full_prompt = f"{system_prompt}\n\n[TASK]\n{user_prompt}"
        response = self._model.generate_content(full_prompt)
        text = response.text.strip()
        tokens = 0
        try:
            tokens = response.usage_metadata.total_token_count
        except Exception:
            pass
        return text, tokens


# ─── OpenAI Provider ──────────────────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """OpenAI GPT models (or any OpenAI-compatible endpoint)."""

    def __init__(self, model: str, generation_config: dict, base_url: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Install openai: pip install openai")
        api_key = os.getenv("OPENAI_API_KEY", "")
        kwargs = {"api_key": api_key or "sk-placeholder"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._temperature = generation_config.get("temperature", 0.2)
        self._max_tokens = generation_config.get("max_output_tokens", 8192)

    def generate(self, system_prompt: str, user_prompt: str) -> tuple[str, int]:
        import time, re
        max_retries = 5
        base_wait   = 10  # seconds

        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                text   = response.choices[0].message.content.strip()
                tokens = response.usage.total_tokens if response.usage else 0
                return text, tokens

            except Exception as e:
                err_str = str(e)
                is_rate_limit = (
                    "429" in err_str
                    or "rate_limit" in err_str.lower()
                    or "RateLimitError" in type(e).__name__
                )
                if is_rate_limit and attempt < max_retries - 1:
                    # Try to parse retry-after seconds from the error message
                    m = re.search(r"try again in\s+([\d.]+)s", err_str, re.IGNORECASE)
                    wait = float(m.group(1)) + 2 if m else base_wait * (2 ** attempt)
                    print(
                        f"\n[RateLimitError] Groq/OpenAI rate limit hit. "
                        f"Waiting {wait:.0f}s before retry {attempt + 1}/{max_retries - 1}..."
                    )
                    time.sleep(wait)
                else:
                    raise


# ─── Anthropic Provider ───────────────────────────────────────────────────────

class AnthropicProvider(LLMProvider):
    """Anthropic Claude models."""

    def __init__(self, model: str, generation_config: dict) -> None:
        try:
            import anthropic
        except ImportError:
            raise ImportError("Install anthropic: pip install anthropic")
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = generation_config.get("max_output_tokens", 8192)

    def generate(self, system_prompt: str, user_prompt: str) -> tuple[str, int]:
        import anthropic
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        tokens = (response.usage.input_tokens + response.usage.output_tokens
                  if response.usage else 0)
        return text, tokens


# ─── Ollama Provider (OpenAI-compatible local) ────────────────────────────────

class OllamaProvider(OpenAIProvider):
    """
    Ollama running locally — uses the OpenAI-compatible API.
    Default base URL: http://localhost:11434/v1
    """

    def __init__(self, model: str, generation_config: dict) -> None:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        super().__init__(model=model, generation_config=generation_config, base_url=base_url)


# ─── Factory ──────────────────────────────────────────────────────────────────

def get_provider(provider_name: str, model: str, generation_config: dict) -> LLMProvider:
    """
    Factory — returns an LLMProvider for the given provider name.

    Args:
        provider_name:     "gemini" | "openai" | "anthropic" | "ollama" | "openai_compat"
        model:             Model name string (e.g. "gpt-4o", "claude-3-5-sonnet-20241022")
        generation_config: Dict with temperature, max_output_tokens, etc.

    Returns:
        Configured LLMProvider instance.
    """
    name = provider_name.lower().strip()

    if name == "gemini":
        return GeminiProvider(model=model, generation_config=generation_config)

    elif name == "openai":
        return OpenAIProvider(model=model, generation_config=generation_config)

    elif name == "anthropic":
        return AnthropicProvider(model=model, generation_config=generation_config)

    elif name == "ollama":
        return OllamaProvider(model=model, generation_config=generation_config)

    elif name == "openai_compat":
        base_url = os.getenv("LLM_BASE_URL")
        if not base_url:
            raise EnvironmentError(
                "LLM_BASE_URL must be set when using provider 'openai_compat'. "
                "Example: LLM_BASE_URL=https://api.groq.com/openai/v1"
            )
        return OpenAIProvider(model=model, generation_config=generation_config, base_url=base_url)

    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. "
            "Choose from: gemini, openai, anthropic, ollama, openai_compat"
        )
