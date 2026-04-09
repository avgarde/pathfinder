"""Layer 3: Flow Generation — extracting meaningful user flows from the
application model and exploration trace.

This layer takes:
- An ApplicationModel (from Layer 2) — the structural understanding
- An exploration trace (from the orchestrator) — what actually happened
- Optionally, a PriorContext (from Layer 0) — external knowledge

And produces:
- A list of Flow objects — coherent user journeys, both observed and
  hypothetical, ranked by importance and frequency

The layer can operate in two modes:

1. Post-exploration (pipeline mode): Given a completed model and full
   trace, generate all flows at once.

2. Incremental (agent loop mode): Called at the end of an exploration
   run to generate flows from whatever was discovered. Can also be
   called with a previously-saved model and trace files.

The AI does the heavy lifting — it identifies which subsequences of
the trace form coherent user journeys, classifies them, and hypothesizes
flows that should exist but weren't observed.
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
    ConcreteStep,
    Flow,
    FlowSet,
    GenerationMetadata,
    SemanticStep,
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
        """Generate flows from a model and exploration trace.

        Args:
            model: The ApplicationModel from Layer 2.
            trace: List of step dicts, each with keys like:
                   step, screen (purpose), action, goal, success,
                   screen_id, observation_id.
            prior_context: Optional prior context for richer analysis.

        Returns:
            A FlowSet containing discovered and hypothetical flows.
        """
        start_time = time.time()

        logger.info(
            "Generating flows from model v%d (%d screens, %d transitions) "
            "and %d trace steps",
            model.model_version,
            len(model.screens),
            len(model.transitions),
            len(trace),
        )

        # Call the AI to identify flows
        raw_flows = await self.ai.generate_flows(
            application_model=model,
            exploration_trace=trace,
            prior_context=prior_context,
        )

        # Parse raw flow dicts into Flow objects
        flows = self._parse_flows(raw_flows)

        duration = time.time() - start_time

        logger.info(
            "Generated %d flows (%.1fs): %d observed, %d hypothetical",
            len(flows),
            duration,
            sum(1 for f in flows if f.validation_status == "validated"),
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
        """Generate flows from previously saved model and summary files.

        This is the pipeline mode entry point — load a model and
        exploration summary from disk and generate flows.
        """
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

    def _parse_flows(self, raw_flows: list[dict[str, Any]]) -> list[Flow]:
        """Convert raw AI output dicts into typed Flow objects."""
        flows: list[Flow] = []

        for flow_data in raw_flows:
            try:
                # Parse category
                cat_str = flow_data.get("category", "core")
                try:
                    category = FlowCategory(cat_str)
                except ValueError:
                    category = FlowCategory.CORE

                # Parse semantic steps
                semantic_steps = []
                for ss in flow_data.get("semantic_steps", []):
                    semantic_steps.append(SemanticStep(
                        step_number=ss.get("step_number", len(semantic_steps) + 1),
                        intent=ss.get("intent", ""),
                        screen_context=ss.get("screen_context", ""),
                        expected_outcome=ss.get("expected_outcome", ""),
                    ))

                # Parse concrete steps (only for observed flows)
                concrete_steps = None
                raw_concrete = flow_data.get("concrete_steps")
                if raw_concrete:
                    concrete_steps = []
                    for cs in raw_concrete:
                        concrete_steps.append(ConcreteStep(
                            step_number=cs.get("step_number", len(concrete_steps) + 1),
                            screen_id=cs.get("screen_id", ""),
                            observation_id=cs.get("observation_id", ""),
                            action_type=cs.get("action_type", ""),
                            action_detail=cs.get("action_detail", {}),
                            result_screen_id=cs.get("result_screen_id", ""),
                            result_observation_id=cs.get("result_observation_id", ""),
                            duration_ms=cs.get("duration_ms", 0),
                        ))

                # Validation status
                status = flow_data.get("validation_status", "hypothetical")
                if status not in ("hypothetical", "validated", "failed"):
                    status = "hypothetical"

                flow = Flow(
                    flow_id=flow_data.get("flow_id", f"flow_{uuid.uuid4().hex[:6]}"),
                    goal=flow_data.get("goal", "Unknown goal"),
                    preconditions=flow_data.get("preconditions", []),
                    postconditions=flow_data.get("postconditions", []),
                    semantic_steps=semantic_steps,
                    category=category,
                    importance=flow_data.get("importance", 0.5),
                    estimated_frequency=flow_data.get("estimated_frequency", 0.5),
                    concrete_steps=concrete_steps,
                    validation_status=status,
                    validation_notes=flow_data.get("validation_notes"),
                    related_capabilities=flow_data.get("related_capabilities", []),
                    sub_flows=flow_data.get("sub_flows"),
                    parent_flow=flow_data.get("parent_flow"),
                )
                flows.append(flow)

                logger.info(
                    "  Flow: %s [%s] importance=%.1f status=%s (%d semantic, %d concrete steps)",
                    flow.goal[:60],
                    flow.category.value,
                    flow.importance,
                    flow.validation_status,
                    len(flow.semantic_steps),
                    len(flow.concrete_steps) if flow.concrete_steps else 0,
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
