"""Layer 2: World Modeling — building and maintaining the ApplicationModel.

This layer takes screen observations (from Layer 1) and an optional
prior context (from Layer 0), and builds/updates a model of what the
application is and what it can do.

The key operations are:
- Screen identification (new screen vs. revisit of known screen)
- Screen graph construction (screens + transitions)
- Entity and capability extraction
- Anomaly detection (novel, absent, contradictory)
- Exploration frontier maintenance

The layer can operate incrementally (one observation at a time in agent
loop mode) or in batch (all observations at once in pipeline mode).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pathfinder.ai.interface import AIInterface
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.application_model import ApplicationModel
from pathfinder.contracts.prior_context import PriorContext
from pathfinder.contracts.screen_observation import ScreenObservation

logger = logging.getLogger(__name__)


class WorldModelingLayer:
    """Layer 2: Build and maintain the application model.

    Standalone invocation (batch mode):
        layer = WorldModelingLayer(ai=my_ai)
        model = await layer.build_model(
            app_ref=AppReference(name="Spotify"),
            observations=observations_list,
            prior_context=prior_context,
        )

    Incremental invocation (agent loop mode):
        layer = WorldModelingLayer(ai=my_ai)
        model = layer.create_empty_model(app_ref)
        model = await layer.update(model, [observation_1], prior_context)
        model = await layer.update(model, [observation_2])
        model = await layer.update(model, [observation_3])
    """

    def __init__(self, ai: AIInterface):
        self.ai = ai

    def create_empty_model(
        self,
        app_ref: AppReference,
        prior_context: PriorContext | None = None,
    ) -> ApplicationModel:
        """Create an initial empty model, optionally seeded from prior context.

        If prior_context is provided, the model starts with the expected
        entities and capabilities from Layer 0. Otherwise it starts blank.
        """
        model = ApplicationModel(app_reference=app_ref)

        if prior_context:
            model.domain = prior_context.category or ""
            model.purpose = prior_context.description
            model.baseline_category = prior_context.category
            model.entities = list(prior_context.expected_entities)
            model.capabilities = list(prior_context.expected_capabilities)

            # Seed the frontier from expected capabilities
            from pathfinder.contracts.application_model import ExplorationHypothesis
            from pathfinder.contracts.common import Provenance

            for cap in prior_context.expected_capabilities:
                model.frontier.append(ExplorationHypothesis(
                    description=f"Find screens related to: {cap.name}",
                    rationale=f"Expected capability from prior context: {cap.description}",
                    provenance=cap.provenance,
                    priority=cap.estimated_importance,
                    search_strategy=f"Look for UI elements related to {cap.name}",
                ))

            logger.info(
                "Created model seeded from prior context: %d capabilities, %d entities, %d frontier items",
                len(model.capabilities),
                len(model.entities),
                len(model.frontier),
            )
        else:
            logger.info("Created empty model")

        return model

    async def update(
        self,
        current_model: ApplicationModel,
        new_observations: list[ScreenObservation],
        prior_context: PriorContext | None = None,
    ) -> ApplicationModel:
        """Update the model with new observations.

        This is the core Layer 2 operation. It sends the current model
        and new observations to the AI, which returns an updated model
        with new screens, transitions, capabilities, entities, anomalies,
        and frontier items.

        Args:
            current_model: The current application model state.
            new_observations: New screen observations to integrate.
            prior_context: Optional prior context (usually only needed
                          on the first update).

        Returns:
            Updated ApplicationModel.
        """
        if not new_observations:
            logger.warning("update() called with no observations")
            return current_model

        logger.info(
            "Updating model (v%d) with %d new observations",
            current_model.model_version,
            len(new_observations),
        )

        updated = await self.ai.update_world_model(
            current_model=current_model,
            new_observations=new_observations,
            prior_context=prior_context,
        )

        logger.info(
            "Model updated to v%d: %d screens, %d transitions, %d capabilities, "
            "%d entities, %d frontier, %d anomalies (coverage=%.0f%%, confidence=%.0f%%)",
            updated.model_version,
            len(updated.screens),
            len(updated.transitions),
            len(updated.capabilities),
            len(updated.entities),
            len(updated.frontier),
            len(updated.anomalies),
            updated.coverage_estimate * 100,
            updated.confidence * 100,
        )

        return updated

    async def build_model(
        self,
        app_ref: AppReference,
        observations: list[ScreenObservation],
        prior_context: PriorContext | None = None,
        batch_size: int = 5,
    ) -> ApplicationModel:
        """Build a model from scratch using a batch of observations.

        This is the pipeline mode entry point. It creates an empty model,
        seeds it from prior context if available, then processes all
        observations in batches.

        Args:
            app_ref: The application reference.
            observations: All screen observations to process.
            prior_context: Optional prior context from Layer 0.
            batch_size: How many observations to process per AI call.
                        Larger batches are more efficient but may exceed
                        token limits for complex screens.

        Returns:
            The completed ApplicationModel.
        """
        model = self.create_empty_model(app_ref, prior_context)

        if not observations:
            logger.warning("build_model() called with no observations")
            return model

        # Process in batches
        for i in range(0, len(observations), batch_size):
            batch = observations[i:i + batch_size]
            logger.info(
                "Processing observation batch %d-%d of %d",
                i + 1, min(i + batch_size, len(observations)), len(observations),
            )
            # Only pass prior_context on the first batch
            ctx = prior_context if i == 0 else None
            model = await self.update(model, batch, ctx)

        return model

    @staticmethod
    def model_to_json(model: ApplicationModel, indent: int = 2) -> str:
        """Serialise an ApplicationModel to JSON string."""
        return model.model_dump_json(indent=indent)

    @staticmethod
    def model_from_json(json_str: str) -> ApplicationModel:
        """Deserialise an ApplicationModel from JSON string."""
        return ApplicationModel.model_validate_json(json_str)

    @staticmethod
    def save_model(model: ApplicationModel, path: str) -> None:
        """Save an ApplicationModel to a JSON file."""
        Path(path).write_text(model.model_dump_json(indent=2))
        logger.info("Model saved to %s", path)

    @staticmethod
    def load_model(path: str) -> ApplicationModel:
        """Load an ApplicationModel from a JSON file."""
        return ApplicationModel.model_validate_json(Path(path).read_text())
