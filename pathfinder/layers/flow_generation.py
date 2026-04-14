"""Layer 3: Flow Generation — extracting meaningful user flows from the
application model and exploration trace.

v2 (Session 4): Updated parser to handle the richer Flow contract.
- Parses FlowStep objects (unified intent + evidence)
- Parses StepAssertion, StepEvidence, EntryCondition, FlowBranch
- Parses TelemetryEventCandidate and DownstreamMappings
- Sets validation_status="candidate" for all AI-generated flows
  (only FlowVerifier may produce "validated" status)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from pathfinder.ai.interface import AIInterface
from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.common import FlowCategory
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
    TelemetryEventCandidate,
)
from pathfinder.contracts.prior_context import PriorContext

logger = logging.getLogger(__name__)


class FlowGenerationLayer:
    """Layer 3: Generate meaningful user flows from model + trace.

    Standalone invocation (pipeline mode):
        layer = FlowGenerationLayer(ai=my_ai)
        flow_set = await layer.generate(
            model=application_model,
            trace=exploration_trace,
            prior_context=prior_context,
        )

    From saved files:
        layer = FlowGenerationLayer(ai=my_ai)
        flow_set = await layer.generate_from_files(
            model_path="final_model.json",
            summary_path="exploration_summary.json",
        )
    """

    def __init__(self, ai: AIInterface):
        self.ai = ai

    async def generate(
        self,
        model: ApplicationModel,
        trace: list[dict[str, Any]],
        prior_context: PriorContext | None = None,
    ) -> FlowSet:
        """Generate flows from a model and exploration trace."""
        start_time = time.time()

        logger.info(
            "Generating flows from model v%d (%d screens, %d transitions) "
            "and %d trace steps",
            model.model_version,
            len(model.screens),
            len(model.transitions),
            len(trace),
        )

        raw_flows = await self.ai.generate_flows(
            application_model=model,
            exploration_trace=trace,
            prior_context=prior_context,
        )

        flows = self._parse_flows(raw_flows)
        duration = time.time() - start_time

        logger.info(
            "Generated %d flows (%.1fs): %d candidate, %d hypothetical",
            len(flows),
            duration,
            sum(1 for f in flows if f.validation_status == "candidate"),
            sum(1 for f in flows if f.validation_status == "hypothetical"),
        )

        flow_set = FlowSet(
            app_reference=model.app_reference,
            application_model=model,
            flows=flows,
            generation_metadata=GenerationMetadata(
                duration_seconds=round(duration, 1),
                ai_calls_made=1,
                screens_explored=len(model.screens),
                exploration_mode="agent_loop",
            ),
        )

        return flow_set

    async def generate_from_files(
        self,
        model_path: str,
        summary_path: str,
        prior_context_path: str | None = None,
    ) -> FlowSet:
        """Generate flows from previously saved model and summary files."""
        from pathfinder.layers.world_modeling import WorldModelingLayer

        model = WorldModelingLayer.load_model(model_path)
        logger.info("Loaded model from %s (v%d)", model_path, model.model_version)

        summary_data = json.loads(Path(summary_path).read_text())
        trace = summary_data.get("steps", [])
        logger.info("Loaded %d trace steps from %s", len(trace), summary_path)

        prior_context = None
        if prior_context_path:
            from pathfinder.layers.context_gathering import ContextGatheringLayer
            prior_context = ContextGatheringLayer.load_context(prior_context_path)

        return await self.generate(model, trace, prior_context)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_assertion(self, data: dict[str, Any], index: int) -> StepAssertion:
        return StepAssertion(
            assertion_id=data.get("assertion_id", f"a_{uuid.uuid4().hex[:6]}"),
            description=data.get("description", ""),
            assertion_type=data.get("assertion_type", "semantic"),
            target=data.get("target"),
            expected_value=data.get("expected_value"),
            is_blocking=data.get("is_blocking", True),
            confidence=float(data.get("confidence", 1.0)),
        )

    def _parse_evidence(self, data: dict[str, Any] | None) -> StepEvidence | None:
        if not data:
            return None
        return StepEvidence(
            observation_id=data.get("observation_id", ""),
            screenshot_path=data.get("screenshot_path"),
            ui_structure_path=data.get("ui_structure_path"),
            relevant_element_ids=data.get("relevant_element_ids", []),
            ai_confidence=float(data.get("ai_confidence", 1.0)),
            execution_verified=bool(data.get("execution_verified", False)),
        )

    def _parse_step(self, data: dict[str, Any], index: int) -> FlowStep:
        pre_assertions = [
            self._parse_assertion(a, i)
            for i, a in enumerate(data.get("pre_assertions", []))
        ]
        post_assertions = [
            self._parse_assertion(a, i)
            for i, a in enumerate(data.get("post_assertions", []))
        ]
        return FlowStep(
            step_number=data.get("step_number", index + 1),
            intent=data.get("intent", ""),
            screen_context=data.get("screen_context", ""),
            expected_outcome=data.get("expected_outcome", ""),
            semantic_target=data.get("semantic_target"),
            action_type=data.get("action_type"),
            action_target_text=data.get("action_target_text"),
            action_input_value=data.get("action_input_value"),
            pre_assertions=pre_assertions,
            post_assertions=post_assertions,
            screen_id=data.get("screen_id"),
            result_screen_id=data.get("result_screen_id"),
            evidence=self._parse_evidence(data.get("evidence")),
            confidence=float(data.get("confidence", 1.0)),
            is_friction_point=bool(data.get("is_friction_point", False)),
            friction_reason=data.get("friction_reason"),
            input_field=data.get("input_field"),
            input_category=data.get("input_category"),
        )

    def _parse_entry_condition(self, data: dict[str, Any]) -> EntryCondition:
        check_data = data.get("check")
        check = self._parse_assertion(check_data, 0) if check_data else None
        return EntryCondition(
            description=data.get("description", ""),
            condition_type=data.get("condition_type", "custom"),
            check=check,
            prerequisite_flow_id=data.get("prerequisite_flow_id"),
            required=bool(data.get("required", True)),
        )

    def _parse_branch(self, data: dict[str, Any]) -> FlowBranch:
        steps = [
            self._parse_step(s, i)
            for i, s in enumerate(data.get("steps", []))
        ]
        return FlowBranch(
            branch_id=data.get("branch_id", f"b_{uuid.uuid4().hex[:6]}"),
            description=data.get("description", ""),
            branch_type=data.get("branch_type", "alternate_path"),
            trigger_condition=data.get("trigger_condition", ""),
            diverges_at_step=int(data.get("diverges_at_step", 1)),
            steps=steps,
            reconnects_at_step=data.get("reconnects_at_step"),
            resolves_to_flow=data.get("resolves_to_flow"),
        )

    def _parse_telemetry_event(self, data: dict[str, Any]) -> TelemetryEventCandidate:
        priority = data.get("priority", "medium")
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        return TelemetryEventCandidate(
            event_name=data.get("event_name", ""),
            trigger_step=int(data.get("trigger_step", 1)),
            trigger_condition=data.get("trigger_condition", ""),
            suggested_properties=data.get("suggested_properties", {}),
            priority=priority,
        )

    def _parse_downstream(self, data: dict[str, Any] | None) -> DownstreamMappings:
        if not data:
            return DownstreamMappings()
        telemetry = [
            self._parse_telemetry_event(e)
            for e in data.get("telemetry_events", [])
        ]
        return DownstreamMappings(
            test_case_ids=data.get("test_case_ids", []),
            requirement_ids=data.get("requirement_ids", []),
            telemetry_events=telemetry,
            funnel_name=data.get("funnel_name"),
            funnel_step_names=data.get("funnel_step_names", []),
        )

    def _parse_flows(self, raw_flows: list[dict[str, Any]]) -> list[Flow]:
        """Convert raw AI output dicts into typed Flow objects."""
        flows: list[Flow] = []

        for flow_data in raw_flows:
            try:
                cat_str = flow_data.get("category", "core")
                try:
                    category = FlowCategory(cat_str)
                except ValueError:
                    category = FlowCategory.CORE

                steps = [
                    self._parse_step(s, i)
                    for i, s in enumerate(flow_data.get("steps", []))
                ]

                # Backward compat: if old-style semantic_steps present and
                # no new steps, convert them minimally
                if not steps:
                    for i, ss in enumerate(flow_data.get("semantic_steps", [])):
                        if isinstance(ss, dict):
                            steps.append(FlowStep(
                                step_number=ss.get("step_number", i + 1),
                                intent=ss.get("intent", ""),
                                screen_context=ss.get("screen_context", ""),
                                expected_outcome=ss.get("expected_outcome", ""),
                            ))

                entry_conditions = [
                    self._parse_entry_condition(c)
                    for c in flow_data.get("entry_conditions", [])
                ]

                # Backward compat: convert old string preconditions
                if not entry_conditions:
                    for p in flow_data.get("preconditions", []):
                        if isinstance(p, str):
                            entry_conditions.append(EntryCondition(
                                description=p,
                                condition_type="custom",
                            ))

                branches = [
                    self._parse_branch(b)
                    for b in flow_data.get("branches", [])
                ]

                downstream = self._parse_downstream(flow_data.get("downstream"))

                # Determine status
                status = flow_data.get("validation_status", "candidate")
                # Only the verifier may set "validated" — AI output gets downgraded
                if status == "validated":
                    status = "candidate"
                if status not in ("candidate", "partial", "failed", "blocked", "hypothetical"):
                    status = "candidate"

                flow = Flow(
                    flow_id=flow_data.get("flow_id", f"flow_{uuid.uuid4().hex[:6]}"),
                    goal=flow_data.get("goal", "Unknown goal"),
                    description=flow_data.get("description", ""),
                    entry_conditions=entry_conditions,
                    required_inputs=flow_data.get("required_inputs", []),
                    success_criteria=flow_data.get("success_criteria", []),
                    exit_state=flow_data.get("exit_state", {}),
                    steps=steps,
                    branches=branches,
                    category=category,
                    importance=float(flow_data.get("importance", 0.5)),
                    estimated_frequency=float(flow_data.get("estimated_frequency", 0.5)),
                    related_capabilities=flow_data.get("related_capabilities", []),
                    friction_score=flow_data.get("friction_score"),
                    evidence_strength=float(flow_data.get("evidence_strength", 0.0)),
                    confidence=float(flow_data.get("confidence", 0.5)),
                    validation_status=status,
                    validation_notes=flow_data.get("validation_notes"),
                    downstream=downstream,
                    sub_flows=flow_data.get("sub_flows"),
                    parent_flow=flow_data.get("parent_flow"),
                    prerequisite_flows=flow_data.get("prerequisite_flows", []),
                    related_flows=flow_data.get("related_flows", []),
                    # Keep old v1 fields for backward compat serialisation
                    preconditions=flow_data.get("preconditions", []),
                    postconditions=flow_data.get("postconditions", []),
                    semantic_steps=flow_data.get("semantic_steps", []),
                    concrete_steps=flow_data.get("concrete_steps"),
                )
                flows.append(flow)

                logger.info(
                    "  Flow: %s [%s] importance=%.1f status=%s evidence=%.1f "
                    "(%d steps, %d branches)",
                    flow.goal[:60],
                    flow.category.value,
                    flow.importance,
                    flow.validation_status,
                    flow.evidence_strength,
                    len(flow.steps),
                    len(flow.branches),
                )

            except Exception as e:
                logger.warning("Failed to parse flow: %s — %s", e, flow_data)

        # Sort by importance (highest first)
        flows.sort(key=lambda f: f.importance, reverse=True)
        return flows

    @staticmethod
    def save_flows(flow_set: FlowSet, path: str) -> None:
        """Save a FlowSet to a JSON file."""
        Path(path).write_text(flow_set.model_dump_json(indent=2))
        logger.info("FlowSet saved to %s", path)

    @staticmethod
    def load_flows(path: str) -> FlowSet:
        """Load a FlowSet from a JSON file."""
        return FlowSet.model_validate_json(Path(path).read_text())

    @staticmethod
    def flows_to_json(flow_set: FlowSet, indent: int = 2) -> str:
        """Serialise a FlowSet to JSON string."""
        return flow_set.model_dump_json(indent=indent)
