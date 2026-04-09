"""ApplicationModel: Layer 2's output — the system's evolving understanding of the app."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.common import Provenance, ScreenType
from pathfinder.contracts.prior_context import Capability, Entity


class ScreenNode(BaseModel):
    """A distinct screen in the app's navigation graph."""

    screen_id: str
    name: str
    screen_type: ScreenType
    purpose: str
    participates_in: list[str] = Field(default_factory=list)  # Capability names
    visit_count: int = 1
    last_observation_id: str = ""


class ScreenTransition(BaseModel):
    """An observed transition between screens."""

    from_screen: str  # screen_id
    to_screen: str  # screen_id
    action: str  # Human-readable
    observed_count: int = 1


class ExplorationHypothesis(BaseModel):
    """Something the system thinks exists but hasn't confirmed."""

    description: str
    rationale: str
    provenance: Provenance
    priority: float = 0.5  # 0.0 to 1.0
    search_strategy: str = ""


class Anomaly(BaseModel):
    """An observation that doesn't fit the current model."""

    description: str
    observation_id: str
    classification: Literal["novel", "absent", "contradictory"]
    exploration_priority_boost: float = 0.0


class ApplicationModel(BaseModel):
    """The system's evolving understanding of the application.
    Output of Layer 2 (World Modeling)."""

    # Identity
    app_reference: AppReference
    model_version: int = 0

    # Domain understanding
    domain: str = ""
    purpose: str = ""
    baseline_category: str | None = None
    differentiators: list[str] = Field(default_factory=list)

    # Entities & capabilities
    entities: list[Entity] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)

    # Screen graph
    screens: list[ScreenNode] = Field(default_factory=list)
    transitions: list[ScreenTransition] = Field(default_factory=list)

    # Exploration state
    frontier: list[ExplorationHypothesis] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    coverage_estimate: float = 0.0
    confidence: float = 0.0
