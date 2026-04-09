"""PriorContext: Layer 0's output — external knowledge gathered before exploration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pathfinder.contracts.common import Provenance


class Capability(BaseModel):
    """Something the app lets a user accomplish."""

    name: str
    description: str
    estimated_importance: float = 0.5  # 0.0 to 1.0
    estimated_frequency: float = 0.5  # 0.0 to 1.0
    is_differentiator: bool = False  # True if this is novel/unique to this app vs. category baseline
    provenance: Provenance = Provenance.INFERRED_CATEGORY


class Entity(BaseModel):
    """A concept or object the app deals with."""

    name: str
    description: str
    provenance: Provenance = Provenance.INFERRED_CATEGORY


class ContextSource(BaseModel):
    """Where a piece of external context came from."""

    source_type: str  # "app_store", "web_search", "user_supplied", "documentation"
    url: str | None = None
    summary: str = ""


class PriorContext(BaseModel):
    """External knowledge gathered before exploration begins.
    Output of Layer 0 (Context Gathering)."""

    app_name: str
    category: str | None = None
    description: str = ""
    expected_capabilities: list[Capability] = Field(default_factory=list)
    expected_entities: list[Entity] = Field(default_factory=list)
    sources: list[ContextSource] = Field(default_factory=list)
