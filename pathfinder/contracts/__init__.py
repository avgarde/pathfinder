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
    DownstreamMappings,
    EntryCondition,
    Flow,
    FlowBranch,
    FlowSet,
    FlowStep,
    FlowVerificationResult,
    GenerationMetadata,
    StepAssertion,
    StepEvidence,
    StepVerificationResult,
    TelemetryEventCandidate,
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
    "ContextSource",
    "Coordinates",
    "DeviceInfo",
    "DownstreamMappings",
    "ElementReference",
    "EntryCondition",
    "Entity",
    "ExplorationHypothesis",
    "Flow",
    "FlowBranch",
    "FlowCategory",
    "FlowSet",
    "FlowStep",
    "FlowVerificationResult",
    "GenerationMetadata",
    "InputCategory",
    "InputRegistry",
    "InputRequest",
    "InputSpec",
    "InputStrategy",
    "NavigationContext",
    "PriorContext",
    "Provenance",
    "ScreenNode",
    "ScreenObservation",
    "ScreenTransition",
    "ScreenType",
    "StepAssertion",
    "StepEvidence",
    "StepVerificationResult",
    "TelemetryEventCandidate",
    "UIElement",
]
