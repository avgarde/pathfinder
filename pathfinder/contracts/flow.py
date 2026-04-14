"""Flow and FlowSet: Layer 3's output — discovered meaningful user journeys.

v2 (Session 4): Major redesign.

Key changes from v1:
- SemanticStep + ConcreteStep merged into FlowStep (every step can have both
  intent AND execution evidence — they are not separate concerns).
- Preconditions/postconditions promoted from plain strings to structured
  EntryCondition / success_criteria for machine-checkable semantics.
- StepAssertion captures verifiable post-conditions per step.
- StepEvidence links each step to a specific observation + screenshot.
- FlowBranch models alternate paths and failure modes explicitly.
- TelemetryEventCandidate makes telemetry instrumentation opportunities
  a first-class output of the flow generation stage.
- DownstreamMappings wires flows to test IDs, requirement IDs, telemetry.
- validation_status extended: candidate → validated/partial/failed/blocked.

Backward-compat shim: `semantic_steps` and `concrete_steps` are kept as
deprecated optional fields so existing serialised FlowSets still load.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.common import DeviceInfo, FlowCategory


# ---------------------------------------------------------------------------
# Step-level evidence and assertions
# ---------------------------------------------------------------------------


class StepAssertion(BaseModel):
    """A verifiable condition that should hold after a step executes.

    The FlowVerifier evaluates each assertion and records pass/fail.
    Blocking assertions gate forward progress; non-blocking ones are
    warnings only.
    """

    assertion_id: str = ""  # Auto-assigned by verifier if empty
    description: str  # Human-readable: "Cart badge shows item count > 0"
    assertion_type: Literal[
        "screen_type_is",      # Screen was classified as a specific ScreenType
        "element_visible",     # A UI element matching target is visible
        "element_enabled",     # A UI element matching target is interactable
        "element_text_contains",  # An element's text contains expected_value
        "text_present",        # Any visible text contains expected_value
        "url_contains",        # Current URL contains expected_value
        "state_changed",       # App state is different from pre-step state
        "semantic",            # Free-form: AI evaluates the assertion description
    ]
    target: str | None = None          # Element label, text, semantic description
    expected_value: str | None = None  # Expected text, URL fragment, type name
    is_blocking: bool = True           # If False, failure is a warning
    confidence: float = 1.0            # How confident is this assertion?


class StepEvidence(BaseModel):
    """Links a flow step to a concrete exploration observation.

    Populated during exploration (partial evidence) or verification
    (full evidence with execution_verified=True).
    """

    observation_id: str
    screenshot_path: str | None = None
    ui_structure_path: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    relevant_element_ids: list[str] = Field(default_factory=list)
    ai_confidence: float = 1.0        # Confidence of the perception AI
    execution_verified: bool = False  # True if replayed by FlowVerifier


# ---------------------------------------------------------------------------
# Step (unified)
# ---------------------------------------------------------------------------


class FlowStep(BaseModel):
    """A single step in a user flow.

    Unifies the old SemanticStep (intent-only) and ConcreteStep (recorded
    action) into a single structure. Every step has an intent description;
    it optionally gains execution evidence when verified.

    The semantic_target field is used by the FlowVerifier for locale-agnostic
    element matching (e.g. "Add to cart button" rather than literal text
    "Add to Cart" — the verifier embeds both and picks the closest match).
    """

    step_number: int

    # Intent
    intent: str              # "Tap the search icon to open the search overlay"
    screen_context: str      # "Product listing screen showing category items"
    expected_outcome: str    # "Search input field is focused and visible"

    # Semantic targeting (for verifier — locale/copy-agnostic)
    semantic_target: str | None = None  # "search icon / magnifying glass button"

    # Concrete action (populated from exploration trace or verifier replay)
    action_type: str | None = None       # "tap", "type", "swipe", "back"
    action_target_text: str | None = None  # Observed label of element acted on
    action_input_value: str | None = None  # For "type" actions

    # Assertions: pre (entry guards) and post (outcomes)
    pre_assertions: list[StepAssertion] = Field(default_factory=list)
    post_assertions: list[StepAssertion] = Field(default_factory=list)

    # Navigation
    screen_id: str | None = None         # Screen this step starts from
    result_screen_id: str | None = None  # Screen after the action

    # Evidence from exploration / verification
    evidence: StepEvidence | None = None

    # Meta
    confidence: float = 1.0
    is_friction_point: bool = False  # e.g. confusing label, hidden CTA
    friction_reason: str | None = None

    # Inputs required at this step
    input_field: str | None = None     # Semantic name: "search_query", "username"
    input_category: str | None = None  # InputCategory string value


# ---------------------------------------------------------------------------
# Entry conditions and success criteria
# ---------------------------------------------------------------------------


class EntryCondition(BaseModel):
    """A structured precondition for a flow to be startable.

    Replaces the old plain-string `preconditions: list[str]`.
    The FlowVerifier checks entry conditions before beginning execution.
    """

    description: str  # "User is logged in"
    condition_type: Literal[
        "app_state",          # App is in a particular runtime state
        "screen_type",        # Current screen matches a ScreenType
        "authenticated",      # User has an active session
        "unauthenticated",    # User is NOT logged in
        "input_available",    # A specific input (credential/data) is supplied
        "prerequisite_flow",  # Another flow must have been completed first
        "custom",
    ]
    # Optional assertion for the verifier to evaluate
    check: StepAssertion | None = None
    # For prerequisite_flow: the flow_id that must be validated first
    prerequisite_flow_id: str | None = None
    required: bool = True  # If False, flow can proceed without it


# ---------------------------------------------------------------------------
# Alternate paths and failure branches
# ---------------------------------------------------------------------------


class FlowBranch(BaseModel):
    """An alternate or failure path that diverges from the happy path.

    Captures what happens when: a precondition is missing, an error
    fires, the user cancels, or an optional detour is taken.
    The FlowVerifier can optionally execute branches to validate them.
    """

    branch_id: str
    description: str
    branch_type: Literal[
        "prerequisite_missing",  # Entry condition was not met
        "error_state",           # App surfaced an error
        "alternate_path",        # Valid but non-primary path to same goal
        "optional_detour",       # Optional sub-task (e.g. fill profile)
        "cancel",                # User bails out
    ]
    trigger_condition: str    # "Login fails with wrong password"
    diverges_at_step: int     # Step number where this branch starts
    steps: list[FlowStep] = Field(default_factory=list)
    reconnects_at_step: int | None = None  # If branch rejoins the happy path
    resolves_to_flow: str | None = None    # If branch leads to a different flow


# ---------------------------------------------------------------------------
# Telemetry and downstream mappings
# ---------------------------------------------------------------------------


class TelemetryEventCandidate(BaseModel):
    """A telemetry event that should be emitted at a step in this flow.

    Produced by the AI during flow generation; refined during verification.
    The ReportGenerator uses these to produce event schema files.
    """

    event_name: str          # Suggested: "checkout.address_entered"
    trigger_step: int        # After which step number this event fires
    trigger_condition: str   # "User taps 'Continue' on the address form"
    suggested_properties: dict[str, str] = Field(default_factory=dict)
    # property_name → description, e.g. {"item_count": "number of cart items"}
    priority: Literal["high", "medium", "low"] = "medium"


class DownstreamMappings(BaseModel):
    """Connections from this flow to other system artifacts.

    Populated progressively: test_case_ids and requirement_ids come from
    the user (via config or annotation); telemetry_events come from AI
    generation; funnel_* come from the usability report.
    """

    # Test traceability
    test_case_ids: list[str] = Field(default_factory=list)
    # Requirement / spec traceability
    requirement_ids: list[str] = Field(default_factory=list)
    # Telemetry instrumentation candidates
    telemetry_events: list[TelemetryEventCandidate] = Field(default_factory=list)
    # Funnel analysis
    funnel_name: str | None = None
    funnel_step_names: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Verification result (attached to a Flow after FlowVerifier runs)
# ---------------------------------------------------------------------------


class StepVerificationResult(BaseModel):
    """The outcome of verifying a single step."""

    step_number: int
    success: bool
    assertion_results: list[dict[str, Any]] = Field(default_factory=list)
    # [{assertion_id, passed, actual_value, error}]
    element_matched: str | None = None  # Which element was actually used
    error: str | None = None
    screenshot_path: str | None = None
    duration_ms: int = 0


class FlowVerificationResult(BaseModel):
    """Complete result of verifying one flow."""

    flow_id: str
    success: bool
    steps_verified: int
    steps_passed: int
    step_results: list[StepVerificationResult] = Field(default_factory=list)
    entry_conditions_met: bool = True
    error: str | None = None
    run_id: str = ""
    started_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Core Flow model
# ---------------------------------------------------------------------------


class Flow(BaseModel):
    """A meaningful user journey through the application.

    v2 redesign: steps are unified FlowStep objects; entry_conditions
    replace plain-string preconditions; validation_status distinguishes
    AI-authored candidates from execution-verified flows.

    Deprecated fields (kept for backward compat): semantic_steps,
    concrete_steps, preconditions, postconditions.
    """

    flow_id: str
    goal: str          # "Complete checkout as a guest user"
    description: str = ""  # Longer narrative description

    # --- Entry / exit conditions ---
    entry_conditions: list[EntryCondition] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    # Semantic names of InputSpec fields required (e.g. "username", "card_number")
    success_criteria: list[str] = Field(default_factory=list)
    # Observable outcomes: "Order confirmation page is shown"
    exit_state: dict[str, str] = Field(default_factory=dict)
    # Key → value pairs describing post-flow app state

    # --- Steps and branches ---
    steps: list[FlowStep] = Field(default_factory=list)
    branches: list[FlowBranch] = Field(default_factory=list)

    # --- Classification ---
    category: FlowCategory = FlowCategory.CORE
    importance: float = 0.5
    estimated_frequency: float = 0.5
    related_capabilities: list[str] = Field(default_factory=list)

    # --- Quality signals ---
    friction_score: float | None = None   # 0.0 (smooth) to 1.0 (very friction-heavy)
    evidence_strength: float = 0.0        # 0.0 (purely hypothetical) to 1.0 (fully verified)
    confidence: float = 0.5              # Overall AI confidence in this flow

    # --- Validation lifecycle ---
    validation_status: Literal[
        "candidate",    # AI-generated; no execution yet
        "validated",    # Fully executed and all assertions passed
        "partial",      # Some steps verified; others blocked/failed
        "failed",       # Execution attempted and failed
        "blocked",      # Entry conditions could not be met
        "hypothetical", # Implied by model structure; not observed during exploration
    ] = "candidate"
    validation_notes: str | None = None
    last_verified_at: datetime | None = None
    validation_run_id: str | None = None
    execution_duration_ms: int = 0

    # Verification detail (attached after FlowVerifier runs)
    verification_result: FlowVerificationResult | None = None

    # --- Downstream ---
    downstream: DownstreamMappings = Field(default_factory=DownstreamMappings)

    # --- Relationships ---
    sub_flows: list[str] | None = None
    parent_flow: str | None = None
    prerequisite_flows: list[str] = Field(default_factory=list)
    related_flows: list[str] = Field(default_factory=list)

    # ---------------------------------------------------------------------------
    # Backward-compatibility: old v1 fields
    # These are kept so that existing serialised FlowSets still deserialise.
    # New code should use `steps`, `entry_conditions`, and `success_criteria`.
    # ---------------------------------------------------------------------------
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    semantic_steps: list[dict] = Field(default_factory=list)
    concrete_steps: list[dict] | None = None


# ---------------------------------------------------------------------------
# FlowSet and generation metadata
# ---------------------------------------------------------------------------


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
