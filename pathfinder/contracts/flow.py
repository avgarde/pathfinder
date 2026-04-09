"""Flow and FlowSet: Layer 3's output — discovered meaningful user journeys."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.common import DeviceInfo, FlowCategory


class SemanticStep(BaseModel):
    """An intent-level step in a flow."""

    step_number: int
    intent: str  # "Select a product from the catalog"
    screen_context: str  # "Product listing screen"
    expected_outcome: str  # "Product detail screen is shown"


class ConcreteStep(BaseModel):
    """A recorded, executable step in a flow."""

    step_number: int
    screen_id: str
    observation_id: str
    action_type: str  # "tap", "type", "swipe", etc.
    action_detail: dict  # Serialised DeviceAction
    result_screen_id: str
    result_observation_id: str
    duration_ms: int = 0


class Flow(BaseModel):
    """A meaningful user journey through the application."""

    flow_id: str

    # Semantic layer
    goal: str
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    semantic_steps: list[SemanticStep] = Field(default_factory=list)
    category: FlowCategory = FlowCategory.CORE
    importance: float = 0.5
    estimated_frequency: float = 0.5

    # Concrete trace
    concrete_steps: list[ConcreteStep] | None = None
    validation_status: Literal["hypothetical", "validated", "failed"] = "hypothetical"
    validation_notes: str | None = None

    # Relationships
    related_capabilities: list[str] = Field(default_factory=list)
    sub_flows: list[str] | None = None
    parent_flow: str | None = None


class GenerationMetadata(BaseModel):
    """How a flow set was produced."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    duration_seconds: float = 0.0
    ai_calls_made: int = 0
    screens_explored: int = 0
    exploration_mode: Literal["pipeline", "agent_loop"] = "pipeline"
    device_info: DeviceInfo | None = None


class FlowSet(BaseModel):
    """The complete output of the system."""

    app_reference: AppReference
    application_model: ApplicationModel
    flows: list[Flow] = Field(default_factory=list)
    generation_metadata: GenerationMetadata = Field(default_factory=GenerationMetadata)
