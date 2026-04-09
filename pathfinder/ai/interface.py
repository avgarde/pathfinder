"""AI interface protocol — all AI model interactions go through this."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.prior_context import PriorContext
from pathfinder.contracts.screen_observation import ScreenObservation


class PerceptionContext:
    """Optional context that improves perception accuracy."""

    def __init__(
        self,
        prior_screen: ScreenObservation | None = None,
        navigation_history: list[str] | None = None,
        known_domain: str | None = None,
        known_entities: list[str] | None = None,
        exploration_focus: str | None = None,
    ):
        self.prior_screen = prior_screen
        self.navigation_history = navigation_history or []
        self.known_domain = known_domain
        self.known_entities = known_entities or []
        self.exploration_focus = exploration_focus


@runtime_checkable
class AIInterface(Protocol):
    """Protocol for all AI model interactions.

    Each method corresponds to a specific cognitive capability the system needs.
    Implementations translate these into model-specific API calls and prompt
    construction. The rest of the system is model-agnostic.

    For the MVP (Layer 1), only `perceive` needs to be implemented.
    """

    async def perceive(
        self,
        screenshot_path: str,
        ui_structure_xml: str | None = None,
        context: PerceptionContext | None = None,
    ) -> ScreenObservation:
        """Given a screenshot (and optional accessibility tree + context),
        return a structured semantic observation of the screen.

        This is the core Layer 1 capability."""
        ...

    async def synthesise_context(
        self,
        app_name: str | None = None,
        package_name: str | None = None,
        raw_description: str | None = None,
        app_store_text: str | None = None,
        web_search_results: str | None = None,
        user_description: str | None = None,
        baseline: str | None = None,
        differentiation: str | None = None,
    ) -> PriorContext:
        """Given gathered raw information about an app, synthesise it into
        a structured PriorContext.

        This is the core Layer 0 AI capability."""
        ...

    async def update_world_model(
        self,
        current_model: ApplicationModel,
        new_observations: list[ScreenObservation],
        prior_context: PriorContext | None = None,
    ) -> ApplicationModel:
        """Given the current world model and new observations, return an
        updated world model.

        This is the core Layer 2 AI capability."""
        ...

    async def generate_flows(
        self,
        application_model: ApplicationModel,
        exploration_trace: list[dict[str, Any]],
        prior_context: PriorContext | None = None,
    ) -> list[dict[str, Any]]:
        """Given an application model and the raw exploration trace, identify
        meaningful user flows.

        The AI analyses the screen graph and concrete step history to:
        1. Extract observed flows (coherent subsequences of the trace)
        2. Hypothesize unobserved flows implied by the model's structure
        3. Classify each flow by category, importance, and frequency

        Returns a list of flow dicts matching the Flow contract schema.
        This is the core Layer 3 AI capability."""
        ...

    async def plan_exploration(
        self,
        current_model: ApplicationModel,
        current_observation: ScreenObservation,
        action_history: list[str] | None = None,
        max_actions_remaining: int = 50,
        available_inputs: dict[str, str] | None = None,
    ) -> ExplorationPlan:
        """Given the current model and what's on screen, decide the next
        exploration action.

        available_inputs maps field names to values (or generate hints)
        that the planner may use when encountering input fields.

        This is the Orchestrator's AI capability — it closes the
        perception→reasoning→action loop."""
        ...


class ExplorationPlan:
    """The AI's decision about what to do next during exploration."""

    def __init__(
        self,
        reasoning: str,
        should_stop: bool,
        stop_reason: str | None,
        action: dict[str, Any],
        expected_outcome: str,
        exploration_goal: str,
        inputs_required: list[dict[str, Any]] | None = None,
    ):
        self.reasoning = reasoning
        self.should_stop = should_stop
        self.stop_reason = stop_reason
        self.action = action  # Raw dict with action_type, target_text, etc.
        self.expected_outcome = expected_outcome
        self.exploration_goal = exploration_goal
        self.inputs_required = inputs_required or []

    def __repr__(self) -> str:
        if self.should_stop:
            return f"ExplorationPlan(STOP: {self.stop_reason})"
        suffix = ""
        if self.inputs_required:
            fields = [ir.get("field", "?") for ir in self.inputs_required]
            suffix = f", needs=[{', '.join(fields)}]"
        return (
            f"ExplorationPlan({self.action.get('action_type', '?')}: "
            f"{self.action.get('description', '?')}{suffix})"
        )
