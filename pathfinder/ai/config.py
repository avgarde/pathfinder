"""AI provider configuration."""

from __future__ import annotations

from pydantic import BaseModel


class AIConfig(BaseModel):
    """Configuration for the AI provider."""

    provider: str = "anthropic"  # "anthropic", "openai_compatible"
    model: str = "claude-sonnet-4-20250514"
    api_key: str | None = None
    base_url: str | None = None  # For local model endpoints
    max_tokens: int = 4096
    temperature: float = 0.0
    planning_budget: int = 50  # Max AI calls per exploration cycle
    timeout: float = 600.0  # HTTP request timeout in seconds (10 min default)
