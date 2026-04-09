"""Pathfinder inter-layer data contracts.

These data structures define the boundaries between layers.
Each is serialisable to/from JSON via Pydantic."""

from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.application_model import (
    Anomaly,
    ApplicationModel,
    ExplorationHypothesis,
    ScreenNode,
    ScreenTransition,
)
from pathfinder.contracts.common import (
    AppInfo,
    Coordinates,
    DeviceInfo,
    ElementReference,
    FlowCategory,
    Provenance,
    ScreenType,
)
from pathfinder.contracts.inputs import (
    InputCategory,
    InputRegistry,
    InputRequest,
    InputSpec,
    InputStrategy,
)
from pathfinder.contracts.flow import (
    ConcreteStep,
    Flow,
    FlowSet,
    GenerationMetadata,
    SemanticStep,
)
from pathfinder.contracts.prior_context import (
    Capability,
    ContextSource,
    Entity,
    PriorContext,
)
from pathfinder.contracts.screen_observation import (
    NavigationContext,
    ScreenObservation,
    UIElement,
)

__all__ = [
    "Anomaly",
    "AppInfo",
    "AppReference",
    "ApplicationModel",
    "Capability",
    "ConcreteStep",
    "ContextSource",
    "Coordinates",
    "DeviceInfo",
    "ElementReference",
    "Entity",
    "ExplorationHypothesis",
    "Flow",
    "InputCategory",
    "InputRegistry",
    "InputRequest",
    "InputSpec",
    "InputStrategy",
    "FlowCategory",
    "FlowSet",
    "GenerationMetadata",
    "NavigationContext",
    "PriorContext",
    "Provenance",
    "ScreenNode",
    "ScreenObservation",
    "ScreenTransition",
    "ScreenType",
    "SemanticStep",
    "UIElement",
]
