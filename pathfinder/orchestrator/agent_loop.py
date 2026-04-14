"""Orchestrator: the agent loop that ties perception, world modeling,
and exploration planning into an autonomous exploration cycle.

The loop:
    1. Perceive the current screen (Layer 1)
    2. Update the world model with the observation (Layer 2)
    3. Ask the AI to plan the next action (exploration planner)
    4. Translate the plan into a DeviceAction and execute it
    5. Repeat until the planner says stop or the budget is exhausted

This is the "agent loop" execution mode described in the architecture.
It interleaves all layers in a tight cycle rather than running them
as sequential pipelines.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pathfinder.ai.interface import AIInterface, ExplorationPlan, PerceptionContext
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.common import ElementReference
from pathfinder.contracts.flow import FlowSet
from pathfinder.events import (
    EventBus,
    ExplorationStarted,
    StepStarted,
    PerceptionComplete,
    ModelUpdated,
    ActionPlanned,
    ActionExecuted,
    InputRequired,
    InputSupplied,
    FlowDetected,
    ExplorationComplete as ExplorationCompleteEvent,
    LogEvent,
    ErrorEvent,
)
from pathfinder.contracts.inputs import (
    InputCategory,
    InputRegistry,
    InputRequest,
    InputSpec,
    InputStrategy,
)
from pathfinder.contracts.prior_context import PriorContext
from pathfinder.contracts.screen_observation import ScreenObservation
from pathfinder.device.actions import (
    BackAction,
    DeviceAction,
    ScrollAction,
    SwipeAction,
    TapAction,
    TypeAction,
    WaitAction,
)
from pathfinder.device.interface import ActionResult
from pathfinder.layers.flow_generation import FlowGenerationLayer
from pathfinder.layers.perception import PerceptionLayer
from pathfinder.layers.world_modeling import WorldModelingLayer

logger = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Generate a unique run ID: timestamp + short random suffix.

    Format: YYYYMMDD-HHMMSS-xxxx (e.g., 20260406-143052-a7f3)
    This gives human-readable ordering by time with uniqueness from the suffix.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{ts}-{suffix}"


@dataclass
class ExplorationConfig:
    """Configures the exploration loop's behaviour."""

    max_actions: int = 30          # Budget: stop after this many actions
    output_dir: str = "./exploration"  # Base dir — runs go under output_dir/<run_id>/
    save_screenshots: bool = True  # Keep every screenshot
    save_observations: bool = True # Keep every observation JSON
    save_model_snapshots: bool = False  # Save model after each step
    settle_time: float = 1.0       # Seconds to wait after each action
    capture_ui_structure: bool = True  # Get DOM/accessibility tree each step
    stuck_threshold: int = 2       # After this many consecutive visits to the
                                   # same screen, consider the loop stuck
    input_specs: list[InputSpec] | None = None  # Pre-supplied input specs
    interactive: bool = False       # Allow "ask" strategy (pause for user input)
    generate_flows: bool = True     # Run Layer 3 after exploration completes
    deep_trace: bool = False        # Save screenshot + page source per step
                                    # into <run_dir>/deeptrace/
    exploration_goals: list[str] | None = None  # Optional goals to pursue.
                                    # e.g. ["find checkout flow", "find account settings"]
                                    # Exploration continues until all goals are
                                    # confirmed OR budget exhausted OR frontier empty.


@dataclass
class ExplorationStep:
    """Record of one step in the exploration loop."""

    step_number: int
    timestamp: float
    observation: ScreenObservation
    plan: ExplorationPlan
    action_executed: str  # Human-readable
    action_result: ActionResult | None = None
    screenshot_path: str = ""
    model_version: int = 0


@dataclass
class ExplorationResult:
    """The complete result of an exploration run."""

    app_reference: AppReference
    model: ApplicationModel
    run_id: str = ""
    run_dir: str = ""  # Actual directory where this run's artifacts live
    steps: list[ExplorationStep] = field(default_factory=list)
    input_requests: list[InputRequest] = field(default_factory=list)
    flow_set: FlowSet | None = None  # Layer 3 output, populated after exploration
    start_time: float = 0.0
    end_time: float = 0.0
    stop_reason: str = ""
    total_actions: int = 0
    confirmed_goals: list[str] = field(default_factory=list)  # Goals confirmed during run

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time


class AgentLoop:
    """The autonomous exploration engine.

    Composes a DeviceAdapter (for interacting with the app), an AI
    interface (for perception, world modeling, and planning), and the
    layer implementations into a single exploration loop.

    Usage:
        loop = AgentLoop(ai=ai, device=adapter, config=config)
        result = await loop.explore(app_ref, prior_context)
    """

    def __init__(
        self,
        ai: AIInterface,
        device: Any,  # DeviceAdapter — using Any to avoid import cycle
        config: ExplorationConfig | None = None,
        event_bus: EventBus | None = None,
        stop_flag: asyncio.Event | None = None,
    ):
        self.ai = ai
        self.device = device
        self.config = config or ExplorationConfig()
        self.event_bus = event_bus or EventBus()
        self.stop_flag = stop_flag

        # Compose the layers
        self.perception = PerceptionLayer(ai=ai, device=device)
        self.world_modeling = WorldModelingLayer(ai=ai)
        self.flow_generation = FlowGenerationLayer(ai=ai)

    async def explore(
        self,
        app_ref: AppReference,
        prior_context: PriorContext | None = None,
        start_url: str | None = None,
        run_id: str | None = None,
        invocation_command: str | None = None,
    ) -> ExplorationResult:
        """Run the full exploration loop.

        Args:
            app_ref: Reference to the application being explored.
            prior_context: Optional prior context from Layer 0.
            start_url: For web apps, the URL to start at. If provided
                       and the device is a WebDeviceAdapter, navigates
                       here first.
            run_id: Unique identifier for this run. Auto-generated if not
                    provided.
            invocation_command: The CLI command that launched this run,
                               logged into the run directory for
                               reproducibility.

        Returns:
            ExplorationResult with the final model and step history.
        """
        # Generate or use provided run ID
        run_id = run_id or generate_run_id()

        # Set up run-specific output directory: <output_dir>/<run_id>/
        base_dir = Path(self.config.output_dir)
        output_dir = base_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Set up run-log: all logging tees to <run_dir>/run-log
        run_log_path = output_dir / "run-log"
        file_handler = logging.FileHandler(str(run_log_path), mode="w")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        # Attach to the root logger so we capture everything
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

        # Log the invocation command for reproducibility
        if invocation_command:
            (output_dir / "invocation").write_text(invocation_command + "\n")
            logger.info("Invocation: %s", invocation_command)
        logger.info("Run ID: %s", run_id)
        logger.info("Run directory: %s", output_dir)

        # Navigate to start URL if this is a web exploration
        if start_url:
            if hasattr(self.device, "navigate_to"):
                logger.info("Navigating to start URL: %s", start_url)
                await self.device.navigate_to(start_url)
            else:
                await self.device.launch_app(start_url)

        # Initialise the world model
        model = self.world_modeling.create_empty_model(app_ref, prior_context)

        result = ExplorationResult(
            app_reference=app_ref,
            model=model,
            run_id=run_id,
            run_dir=str(output_dir),
            start_time=time.time(),
        )

        # Input handling
        prompt_fn = None
        if self.config.interactive:
            prompt_fn = self._interactive_prompt

        input_registry = InputRegistry(
            specs=self.config.input_specs,
            interactive=self.config.interactive,
            prompt_fn=prompt_fn,
        )
        def _build_available_inputs() -> dict[str, str]:
            """Build the available_inputs dict for the planner.

            Maps field→value for literal specs, field→"generate: <hint>"
            for generate specs, and field→cached_value for ask specs
            that have already been answered. Rebuilt each iteration so
            interactive answers appear as they're provided.
            """
            inputs: dict[str, str] = {}
            if self.config.input_specs:
                for spec in self.config.input_specs:
                    if spec.strategy == InputStrategy.LITERAL and spec.value:
                        display = spec.value if not spec.sensitive else "••••••••"
                        inputs[spec.field] = display
                    elif spec.strategy == InputStrategy.GENERATE:
                        inputs[spec.field] = (
                            f"generate: {spec.generate_hint or spec.field}"
                        )
                    elif spec.strategy == InputStrategy.ASK:
                        # Include cached answers from previous interactive prompts
                        cached = input_registry._ask_cache.get(spec.field)
                        if cached:
                            inputs[spec.field] = cached
            return inputs

        available_inputs = _build_available_inputs()
        if available_inputs:
            logger.info(
                "Available inputs: %s",
                ", ".join(available_inputs.keys()),
            )

        action_history: list[str] = []
        actions_taken = 0
        self._pending_navigation: str | None = None

        # Goal tracking: set of goals confirmed so far
        confirmed_goals: set[str] = set()
        exploration_goals: list[str] = list(self.config.exploration_goals or [])

        # Loop detection state.
        # _screen_fingerprints tracks a short signature of each screen seen,
        # derived from (screen_type, first ~80 chars of screen_purpose).
        # _consecutive_same counts how many steps in a row the same fingerprint
        # appeared. _dead_ends records fingerprints that we've tried and failed
        # to make progress on, so the planner knows to avoid them.
        last_fingerprint: str = ""
        consecutive_same: int = 0
        dead_ends: set[str] = set()

        # Graph-level cycle detection: sliding window of recent fingerprints
        # Detects A→B→A→B patterns that same-screen detection misses
        _recent_fingerprints: list[str] = []
        _CYCLE_WINDOW = 8  # look back 8 steps

        logger.info(
            "Starting exploration of %s (budget: %d actions)",
            app_ref.name or app_ref.web_url or "app",
            self.config.max_actions,
        )

        # --- EVENT: Exploration Started ---
        await self.event_bus.emit(ExplorationStarted(
            run_id=run_id,
            target_url=start_url or app_ref.web_url or "",
            target_name=app_ref.name or "",
            max_actions=self.config.max_actions,
            config={
                "headless": getattr(self.device, "headless", None),
                "browser_type": getattr(self.device, "browser_type", None),
                "deep_trace": self.config.deep_trace,
                "interactive": self.config.interactive,
            },
        ))

        try:
            while actions_taken < self.config.max_actions:
                step_num = actions_taken + 1
                logger.info("=== Step %d / %d ===", step_num, self.config.max_actions)

                # Check for external stop request (from IDE or other controller)
                if self.stop_flag is not None and self.stop_flag.is_set():
                    logger.info("Stop flag set — terminating exploration gracefully")
                    result.stop_reason = "Stop requested by user"
                    break

                # --- EVENT: Step Started ---
                await self.event_bus.emit(StepStarted(step=step_num))

                # --- 1. PERCEIVE ---
                screenshot_path = str(output_dir / f"step_{step_num:03d}.png")

                # Build perception context from what we know so far
                prior_obs = result.steps[-1].observation if result.steps else None
                perception_ctx = PerceptionContext(
                    prior_screen=prior_obs,
                    navigation_history=action_history[-5:],
                    known_domain=model.domain or None,
                    known_entities=[e.name for e in model.entities[:10]],
                    exploration_focus=(
                        model.frontier[0].description if model.frontier else None
                    ),
                )

                observation = await self.perception.perceive_live(
                    output_dir=str(output_dir),
                    capture_ui_structure=self.config.capture_ui_structure,
                    context=perception_ctx,
                )

                # Move screenshot to the step-specific name
                default_screenshot = output_dir / "screenshot.png"
                if default_screenshot.exists():
                    import shutil
                    shutil.move(str(default_screenshot), screenshot_path)
                    observation.screenshot_path = screenshot_path

                if self.config.save_observations:
                    obs_path = str(output_dir / f"step_{step_num:03d}_observation.json")
                    PerceptionLayer.save_observation(observation, obs_path)

                # --- DEEP TRACE ---
                if self.config.deep_trace:
                    deeptrace_dir = output_dir / "deeptrace"
                    deeptrace_dir.mkdir(exist_ok=True)

                    # Copy screenshot into deeptrace
                    import shutil as _shutil
                    dt_screenshot = deeptrace_dir / f"step_{step_num:03d}.png"
                    if Path(screenshot_path).exists():
                        _shutil.copy2(screenshot_path, str(dt_screenshot))

                    # Capture page source if the adapter supports it
                    if hasattr(self.device, "get_page_source"):
                        try:
                            page_source = await self.device.get_page_source()
                            dt_source = deeptrace_dir / f"step_{step_num:03d}.html"
                            dt_source.write_text(page_source, encoding="utf-8")
                            logger.debug(
                                "Deep trace: saved %d bytes of page source for step %d",
                                len(page_source), step_num,
                            )
                        except Exception as e:
                            logger.warning("Deep trace: failed to capture page source: %s", e)

                logger.info(
                    "Perceived: %s (%s, %d elements)",
                    observation.screen_purpose,
                    observation.screen_type.value,
                    len(observation.elements),
                )

                # --- EVENT: Perception Complete ---
                page_url = ""
                page_title = ""
                if hasattr(self.device, "page") and self.device._page:
                    try:
                        page_url = self.device.page.url
                        page_title = await self.device.page.title()
                    except Exception:
                        pass
                await self.event_bus.emit(PerceptionComplete(
                    step=step_num,
                    screenshot_path=screenshot_path,
                    page_url=page_url,
                    page_title=page_title,
                    screen_id=observation.screen_type.value,
                    screen_description=observation.screen_purpose,
                    ui_summary=f"{len(observation.elements)} elements, {sum(1 for e in observation.elements if e.is_interactive)} interactive",
                ))

                # --- LOOP DETECTION ---
                fingerprint = self._screen_fingerprint(observation)
                if fingerprint == last_fingerprint:
                    consecutive_same += 1
                else:
                    consecutive_same = 1
                    last_fingerprint = fingerprint

                is_stuck = consecutive_same > self.config.stuck_threshold
                if is_stuck:
                    dead_ends.add(fingerprint)
                    logger.warning(
                        "STUCK: seen screen '%s' %d times consecutively",
                        fingerprint, consecutive_same,
                    )

                # --- GRAPH CYCLE DETECTION ---
                _recent_fingerprints.append(fingerprint)
                if len(_recent_fingerprints) > _CYCLE_WINDOW:
                    _recent_fingerprints.pop(0)

                # Detect 2-screen cycles: [A, B, A, B] in last 4 steps
                in_2cycle = False
                if len(_recent_fingerprints) >= 4:
                    last4 = _recent_fingerprints[-4:]
                    if last4[0] == last4[2] and last4[1] == last4[3] and last4[0] != last4[1]:
                        in_2cycle = True
                        logger.warning(
                            "2-CYCLE DETECTED: alternating between '%s' and '%s'",
                            last4[0][:40], last4[1][:40],
                        )
                        dead_ends.add(last4[0])
                        dead_ends.add(last4[1])

                # Detect 3-screen cycles: [A, B, C, A, B, C] in last 6 steps
                in_3cycle = False
                if len(_recent_fingerprints) >= 6:
                    last6 = _recent_fingerprints[-6:]
                    if last6[:3] == last6[3:]:
                        in_3cycle = True
                        logger.warning(
                            "3-CYCLE DETECTED: repeating pattern %s",
                            [fp[:30] for fp in last6[:3]],
                        )
                        for fp in last6[:3]:
                            dead_ends.add(fp)

                # --- 2. UPDATE WORLD MODEL ---
                ctx_for_update = prior_context if step_num == 1 else None
                model = await self.world_modeling.update(
                    model, [observation], ctx_for_update
                )

                # --- EVENT: Model Updated ---
                prev_screens = len(model.screens) - (1 if step_num == 1 else 0)
                await self.event_bus.emit(ModelUpdated(
                    step=step_num,
                    screens_count=len(model.screens),
                    transitions_count=len(model.transitions),
                    capabilities_count=len(model.capabilities),
                    coverage_estimate=model.coverage_estimate,
                    new_screen=len(model.screens) > prev_screens,
                    new_transitions=0,  # Would need diff tracking for precision
                    screen_id=observation.screen_type.value,
                ))

                if self.config.save_model_snapshots:
                    model_path = str(output_dir / f"model_v{model.model_version}.json")
                    WorldModelingLayer.save_model(model, model_path)

                # --- 3. PLAN NEXT ACTION ---
                # Build an enriched action history that tells the planner what
                # screen each action was taken from and whether it resulted in
                # any screen change, so it can see "I did X on screen Y three
                # times and nothing changed".
                enriched_history = list(action_history)
                if is_stuck:
                    enriched_history.append(
                        f"⚠ STUCK: This screen ({observation.screen_type.value}: "
                        f"{observation.screen_purpose[:60]}) has been seen "
                        f"{consecutive_same} times in a row. Previous actions on "
                        f"this screen did NOT navigate away. You MUST either "
                        f"navigate back or try a completely different part of the "
                        f"app. Do NOT repeat any action already in the history."
                    )
                if in_2cycle or in_3cycle:
                    cycle_type = "2-screen" if in_2cycle else "3-screen"
                    cycle_fps = _recent_fingerprints[-4:] if in_2cycle else _recent_fingerprints[-6:]
                    enriched_history.append(
                        f"⚠ {cycle_type.upper()} CYCLE DETECTED: The exploration is oscillating "
                        f"between screens. You MUST navigate to a completely different part of "
                        f"the app — use a top-level navigation item, go to the home screen, "
                        f"or navigate to an unvisited URL. Do NOT continue the same pattern."
                    )
                if dead_ends:
                    de_list = ", ".join(sorted(dead_ends))
                    enriched_history.append(
                        f"⚠ DEAD ENDS (screens where we got stuck and should "
                        f"avoid returning to): {de_list}"
                    )

                # Rebuild available_inputs each step to pick up
                # interactive answers resolved since the last iteration.
                available_inputs = _build_available_inputs()

                plan = await self.ai.plan_exploration(
                    current_model=model,
                    current_observation=observation,
                    action_history=enriched_history,
                    max_actions_remaining=self.config.max_actions - actions_taken,
                    available_inputs=available_inputs or None,
                    exploration_goals=exploration_goals or None,
                    confirmed_goals=list(confirmed_goals) if confirmed_goals else None,
                )

                # --- GOAL TRACKING ---
                if plan.goals_confirmed:
                    for g in plan.goals_confirmed:
                        if g and g not in confirmed_goals:
                            confirmed_goals.add(g)
                            logger.info("✓ Goal confirmed: %s", g)
                    if exploration_goals and confirmed_goals.issuperset(set(exploration_goals)):
                        logger.info(
                            "All exploration goals confirmed (%d/%d). "
                            "Exploration will stop after this step.",
                            len(confirmed_goals), len(exploration_goals),
                        )

                logger.info("Plan: %s", plan)

                # --- EVENT: Action Planned ---
                await self.event_bus.emit(ActionPlanned(
                    step=step_num,
                    action_type=plan.action.get("action_type", "unknown") if plan.action else "stop",
                    action_description=plan.action.get("description", "") if plan.action else "stop",
                    reasoning=plan.reasoning or "",
                    raw_plan=plan.action or {},
                ))

                # --- RECORD INPUT REQUESTS ---
                if plan.inputs_required:
                    for ir_data in plan.inputs_required:
                        try:
                            cat = ir_data.get("category", "other")
                            try:
                                category = InputCategory(cat)
                            except ValueError:
                                category = InputCategory.OTHER

                            request = InputRequest(
                                field=ir_data.get("field", "unknown"),
                                category=category,
                                screen_type=observation.screen_type.value,
                                screen_purpose=observation.screen_purpose[:120],
                                element_label=ir_data.get("element_label", ""),
                                element_type=ir_data.get("element_type", ""),
                                placeholder=ir_data.get("placeholder", ""),
                                required=ir_data.get("required", True),
                                step_number=step_num,
                                suggested_strategy=(
                                    InputStrategy.LITERAL
                                    if category == InputCategory.CREDENTIALS
                                    else InputStrategy.GENERATE
                                    if category == InputCategory.SEARCH_QUERY
                                    else InputStrategy.SKIP
                                ),
                                notes=ir_data.get("notes", ""),
                            )
                            input_registry.record(request)
                            logger.info(
                                "Input required: %s (%s) on %s",
                                request.field, request.category, request.screen_type,
                            )
                            # --- EVENT: Input Required ---
                            await self.event_bus.emit(InputRequired(
                                step=step_num,
                                field_name=request.field,
                                field_type=request.element_type,
                                context=f"{request.element_label} on {request.screen_purpose[:60]}",
                            ))
                        except Exception as e:
                            logger.warning("Failed to parse input request: %s", e)

                # --- RESOLVE "ASK" INPUTS ---
                # If any newly-discovered inputs have the "ask" strategy,
                # resolve them now. The answers will be picked up by
                # _build_available_inputs on the next iteration.
                if plan.inputs_required and self.config.interactive:
                    for ir_data in plan.inputs_required:
                        field = ir_data.get("field", "")
                        if field and input_registry.get_strategy(field) == InputStrategy.ASK:
                            context = (
                                f"{ir_data.get('element_label', '')} on "
                                f"{observation.screen_purpose[:60]}"
                            )
                            input_registry.resolve(field, context)

                # If stuck for too long, override the planner and force a back
                if (is_stuck and consecutive_same > self.config.stuck_threshold + 2) or in_2cycle or in_3cycle:
                    logger.warning(
                        "Force-navigating back: stuck for %d steps",
                        consecutive_same,
                    )
                    plan = ExplorationPlan(
                        reasoning=f"Orchestrator override: stuck on same screen for {consecutive_same} steps",
                        should_stop=False,
                        stop_reason=None,
                        action={
                            "action_type": "back",
                            "description": f"Forced back — stuck on {fingerprint}",
                        },
                        expected_outcome="Return to a previously-visited screen",
                        exploration_goal="Escape stuck state",
                    )

                # Check if the planner says to stop
                if plan.should_stop:
                    logger.info("Planner says stop: %s", plan.stop_reason)

                    step = ExplorationStep(
                        step_number=step_num,
                        timestamp=time.time(),
                        observation=observation,
                        plan=plan,
                        action_executed="STOP",
                        screenshot_path=screenshot_path,
                        model_version=model.model_version,
                    )
                    result.steps.append(step)
                    result.stop_reason = plan.stop_reason or "Planner decided to stop"
                    break

                # --- 4. TRANSLATE PLAN TO ACTION AND EXECUTE ---
                device_action = self._translate_plan_to_action(plan)
                action_desc = plan.action.get("description", str(device_action))
                logger.info("Executing: %s", action_desc)

                # Tag the action with its source screen for history readability
                screen_tag = f"[on {observation.screen_type.value}: {observation.screen_purpose[:50]}]"
                action_history_entry = f"{action_desc} {screen_tag}"

                # Handle navigate actions specially for web
                if self._pending_navigation:
                    nav_url = self._pending_navigation
                    self._pending_navigation = None
                    try:
                        await self.device.navigate_to(nav_url)
                        action_result = ActionResult(
                            success=True, message=f"Navigated to {nav_url}"
                        )
                    except Exception as e:
                        action_result = ActionResult(
                            success=False, message=f"Navigation failed: {e}"
                        )
                else:
                    action_result = await self.device.perform_action(device_action)

                if not action_result.success:
                    logger.warning("Action failed: %s", action_result.message)
                    action_history_entry += f" [FAILED: {action_result.message}]"

                # --- EVENT: Action Executed ---
                await self.event_bus.emit(ActionExecuted(
                    step=step_num,
                    action_type=plan.action.get("action_type", "unknown") if plan.action else "unknown",
                    action_description=action_desc,
                    success=action_result.success,
                    result_message=action_result.message or "",
                ))

                action_history.append(action_history_entry)
                actions_taken += 1

                step = ExplorationStep(
                    step_number=step_num,
                    timestamp=time.time(),
                    observation=observation,
                    plan=plan,
                    action_executed=action_desc,
                    action_result=action_result,
                    screenshot_path=screenshot_path,
                    model_version=model.model_version,
                )
                result.steps.append(step)

                # --- 5. SETTLE ---
                await asyncio.sleep(self.config.settle_time)

        except KeyboardInterrupt:
            logger.info("Exploration interrupted by user")
            result.stop_reason = "User interrupted"
        except Exception as e:
            logger.error("Exploration error: %s", e, exc_info=True)
            result.stop_reason = f"Error: {e}"

        if not result.stop_reason:
            result.stop_reason = "Budget exhausted"

        result.model = model
        result.end_time = time.time()
        result.total_actions = actions_taken
        result.input_requests = input_registry.requests
        result.confirmed_goals = list(confirmed_goals)

        # Save final model
        final_model_path = str(output_dir / "final_model.json")
        WorldModelingLayer.save_model(model, final_model_path)

        # Save input requirements discovered during this run
        if input_registry.requests:
            inputs_path = str(output_dir / "inputs_required.json")
            Path(inputs_path).write_text(input_registry.to_requests_json())
            logger.info(
                "Saved %d input requirements to %s",
                len(input_registry.requests), inputs_path,
            )

        # --- LAYER 3: FLOW GENERATION ---
        if self.config.generate_flows and result.steps:
            try:
                logger.info("Running Layer 3: Flow Generation...")
                # Build the trace from exploration steps
                trace = [
                    {
                        "step": s.step_number,
                        "screen": s.observation.screen_purpose,
                        "screen_type": s.observation.screen_type.value,
                        "action": s.action_executed,
                        "goal": s.plan.exploration_goal,
                        "success": s.action_result.success if s.action_result else None,
                    }
                    for s in result.steps
                ]
                flow_set = await self.flow_generation.generate(
                    model=model,
                    trace=trace,
                    prior_context=prior_context,
                )
                result.flow_set = flow_set

                # Save flows
                flows_path = str(output_dir / "flows.json")
                FlowGenerationLayer.save_flows(flow_set, flows_path)
                logger.info(
                    "Generated %d flows, saved to %s",
                    len(flow_set.flows), flows_path,
                )

                # --- EVENT: Flow Detected (one per flow) ---
                for f in flow_set.flows:
                    await self.event_bus.emit(FlowDetected(
                        flow_name=f.goal,
                        flow_category=f.category.value if hasattr(f.category, "value") else str(f.category),
                        flow_type=f.validation_status,
                        importance=f.importance,
                        step_count=len(f.steps),
                        description=f.description or (f.success_criteria[0] if f.success_criteria else ""),
                    ))
            except Exception as e:
                logger.error("Flow generation failed: %s", e, exc_info=True)

        # Save exploration summary
        self._save_summary(result, str(output_dir / "exploration_summary.json"))

        logger.info(
            "Exploration complete: %d actions in %.1fs. "
            "Model v%d: %d screens, %d transitions, coverage=%.0f%%. "
            "Stop reason: %s",
            result.total_actions,
            result.duration_seconds,
            model.model_version,
            len(model.screens),
            len(model.transitions),
            model.coverage_estimate * 100,
            result.stop_reason,
        )

        # --- EVENT: Exploration Complete ---
        await self.event_bus.emit(ExplorationCompleteEvent(
            run_id=run_id,
            stop_reason=result.stop_reason,
            total_actions=result.total_actions,
            duration_seconds=result.duration_seconds,
            screens_count=len(model.screens),
            transitions_count=len(model.transitions),
            flows_count=len(result.flow_set.flows) if result.flow_set else 0,
            model_path=final_model_path,
            summary_path=str(output_dir / "exploration_summary.json"),
            flows_path=str(output_dir / "flows.json") if result.flow_set else "",
        ))

        # Flush and remove the run-log file handler
        file_handler.flush()
        file_handler.close()
        root_logger.removeHandler(file_handler)

        return result

    @staticmethod
    def _interactive_prompt(field: str, context: str = "") -> str | None:
        """Prompt the user for input via stdin.

        This is the callback passed to InputRegistry when --interactive
        is active. It pauses the exploration loop and asks the user to
        provide a value for a specific field.

        Returns the user's input, or None if they enter an empty string
        (which signals "skip this field").
        """
        print()  # noqa: T201
        print(f"┌─ Input Required ─────────────────────────")  # noqa: T201
        print(f"│ Field: {field}")  # noqa: T201
        if context:
            print(f"│ Context: {context}")  # noqa: T201
        print(f"│ (press Enter with no value to skip)")  # noqa: T201
        print(f"└──────────────────────────────────────────")  # noqa: T201
        try:
            value = input(f"  {field}: ").strip()
            if value:
                logger.info("Interactive input for '%s': (provided)", field)
                return value
            else:
                logger.info("Interactive input for '%s': (skipped)", field)
                return None
        except (EOFError, KeyboardInterrupt):
            logger.info("Interactive input for '%s': (cancelled)", field)
            return None

    @staticmethod
    def _screen_fingerprint(obs: ScreenObservation) -> str:
        """Derive a short, stable fingerprint from a screen observation.

        Two observations of the "same" screen (even with different dynamic
        content like timestamps or story titles) should produce the same
        fingerprint.

        The AI's prose description varies slightly between calls ("login
        screen" vs "login/registration screen"), so we cannot rely on
        exact text. Instead, we use a structural signature:
          screen_type + number of interactive elements + sorted element types

        This is stable across AI rewording and content changes (different
        stories on HN) but distinguishes structurally different screens
        (a list with 12 links vs a form with 4 inputs).
        """
        interactive = sorted(
            e.element_type for e in obs.elements if e.is_interactive
        )
        # Collapse to a compact string: "login|7|button,button,link,text_field"
        return f"{obs.screen_type.value}|{len(interactive)}|{','.join(interactive)}"

    def _translate_plan_to_action(self, plan: ExplorationPlan) -> DeviceAction:
        """Convert the AI's exploration plan into a concrete DeviceAction.

        The plan's action dict contains fields like action_type, target_text,
        target_role, input_text, direction, url. We build the appropriate
        typed action from these.

        Defensively handles malformed plans — smaller local models sometimes
        omit required fields or return unexpected types. Any failure here
        degrades to a WaitAction rather than crashing the run.
        """
        try:
            return self._translate_plan_to_action_inner(plan)
        except Exception as e:
            logger.warning("Failed to translate plan to action: %s — defaulting to wait", e)
            return WaitAction(timeout_ms=1000, description=f"Plan translation error: {e}")

    def _translate_plan_to_action_inner(self, plan: ExplorationPlan) -> DeviceAction:
        """Inner implementation of plan translation (may raise on bad data)."""
        action_data = plan.action
        if not action_data or not isinstance(action_data, dict):
            logger.warning("Empty or invalid action data, defaulting to wait")
            return WaitAction(timeout_ms=1000, description="Invalid action from planner")

        action_type = action_data.get("action_type") or "wait"
        target_text = action_data.get("target_text") or None
        target_role = action_data.get("target_role") or None
        target_desc = action_data.get("target_description") or ""
        description = action_data.get("description") or ""

        if action_type == "tap":
            if not target_text and not target_desc:
                logger.warning("Tap action with no target, converting to wait")
                return WaitAction(timeout_ms=500, description=f"Skipped targetless tap: {description}")
            target = ElementReference(
                text=target_text,
                content_description=target_desc if not target_text else None,
                description=target_desc,
            )
            return TapAction(target=target, description=description)

        elif action_type == "type":
            input_text = action_data.get("input_text") or ""
            if not input_text:
                # The AI planned a type action but gave no text — skip it
                # rather than crashing. Common with smaller local models.
                logger.warning("Type action with no input_text, converting to wait")
                return WaitAction(timeout_ms=500, description=f"Skipped empty type: {description}")
            target = None
            if target_text or target_desc:
                target = ElementReference(
                    text=target_text,
                    content_description=target_desc if not target_text else None,
                    description=target_desc,
                )
            return TypeAction(text=input_text, target=target, description=description)

        elif action_type == "scroll":
            direction = action_data.get("direction", "down")
            if direction not in ("up", "down"):
                direction = "down"
            return ScrollAction(direction=direction, description=description)

        elif action_type == "swipe":
            direction = action_data.get("direction", "up")
            if direction not in ("up", "down", "left", "right"):
                direction = "up"
            return SwipeAction(direction=direction, description=description)

        elif action_type == "back":
            return BackAction(description=description)

        elif action_type == "navigate":
            # For web: navigate is a special tap-like action on a URL.
            # The web adapter's navigate_to handles this, but we need to
            # go through perform_action. We'll use a tap on nothing and
            # handle the navigation explicitly.
            url = action_data.get("url", "")
            if url and hasattr(self.device, "navigate_to"):
                # Create a coroutine that will be awaited separately
                # For now, model as a wait + the caller handles navigation
                # Actually, let's just add a NavigateAction or handle inline
                logger.info("Navigation requested to: %s", url)
                # Store URL for the caller to handle. Use WaitAction as a
                # placeholder since we'll navigate before the next perceive.
                self._pending_navigation = url
                return WaitAction(timeout_ms=100, description=f"Navigate to {url}")
            return WaitAction(timeout_ms=1000, description=description)

        elif action_type == "wait":
            return WaitAction(timeout_ms=2000, description=description)

        else:
            logger.warning("Unknown action type '%s', defaulting to wait", action_type)
            return WaitAction(timeout_ms=1000, description=f"Unknown: {action_type}")

    def _save_summary(self, result: ExplorationResult, path: str) -> None:
        """Save a JSON summary of the exploration run."""
        import json

        summary = {
            "run_id": result.run_id,
            "run_dir": result.run_dir,
            "app": result.app_reference.name or result.app_reference.web_url,
            "total_actions": result.total_actions,
            "duration_seconds": round(result.duration_seconds, 1),
            "stop_reason": result.stop_reason,
            "model_version": result.model.model_version,
            "screens_discovered": len(result.model.screens),
            "transitions_discovered": len(result.model.transitions),
            "capabilities": len(result.model.capabilities),
            "coverage_estimate": result.model.coverage_estimate,
            "confidence": result.model.confidence,
            "inputs_required": [
                {
                    "field": ir.field,
                    "category": ir.category,
                    "screen_type": ir.screen_type,
                    "element_label": ir.element_label,
                    "required": ir.required,
                    "suggested_strategy": ir.suggested_strategy,
                    "notes": ir.notes,
                }
                for ir in result.input_requests
            ],
            "exploration_goals": list(self.config.exploration_goals or []),
            "confirmed_goals": result.confirmed_goals,
            "flows": (
                [
                    {
                        "flow_id": f.flow_id,
                        "goal": f.goal,
                        "category": f.category.value if hasattr(f.category, 'value') else f.category,
                        "importance": f.importance,
                        "status": f.validation_status,
                        "steps": len(f.steps),
                    }
                    for f in result.flow_set.flows
                ]
                if result.flow_set
                else []
            ),
            "steps": [
                {
                    "step": s.step_number,
                    "screen": s.observation.screen_purpose,
                    "action": s.action_executed,
                    "goal": s.plan.exploration_goal,
                    "success": s.action_result.success if s.action_result else None,
                }
                for s in result.steps
            ],
        }

        Path(path).write_text(json.dumps(summary, indent=2))
        logger.info("Exploration summary saved to %s", path)
