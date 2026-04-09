"""Layer 1: Perception — producing ScreenObservations from app screens.

This layer composes the AI interface and device adapter to perceive
what's on screen. It can operate in two modes:

1. Live mode: captures a screenshot from a connected device, optionally
   extracts the UI hierarchy, and sends both to the AI for analysis.

2. Offline mode: analyses a pre-captured screenshot (and optional UI
   hierarchy file) without a device connection.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pathfinder.ai.interface import AIInterface, PerceptionContext
from pathfinder.contracts.screen_observation import ScreenObservation
from pathfinder.device.interface import DeviceAdapter

logger = logging.getLogger(__name__)


class PerceptionLayer:
    """Layer 1: Perceive app screens and produce structured observations.

    Standalone invocation:
        layer = PerceptionLayer(ai=my_ai_interface)
        observation = await layer.perceive_screenshot("screen.png")

    Live device mode:
        layer = PerceptionLayer(ai=my_ai_interface, device=my_device_adapter)
        observation = await layer.perceive_live(output_dir="./captures")
    """

    def __init__(
        self,
        ai: AIInterface,
        device: DeviceAdapter | None = None,
    ):
        self.ai = ai
        self.device = device

    async def perceive_screenshot(
        self,
        screenshot_path: str,
        ui_structure_xml: str | None = None,
        context: PerceptionContext | None = None,
    ) -> ScreenObservation:
        """Offline mode: analyse a pre-captured screenshot.

        Args:
            screenshot_path: Path to the screenshot image file.
            ui_structure_xml: Optional accessibility tree XML string.
            context: Optional perception context for richer analysis.

        Returns:
            A ScreenObservation with the structured analysis.
        """
        logger.info("Perceiving screenshot: %s", screenshot_path)

        # If ui_structure_xml is a file path, read it
        if ui_structure_xml and Path(ui_structure_xml).exists():
            ui_structure_xml = Path(ui_structure_xml).read_text()

        observation = await self.ai.perceive(
            screenshot_path=screenshot_path,
            ui_structure_xml=ui_structure_xml,
            context=context,
        )

        logger.info(
            "Perceived: %s (type=%s, %d elements, confidence=%.2f)",
            observation.screen_purpose,
            observation.screen_type.value,
            len(observation.elements),
            observation.confidence,
        )

        return observation

    async def perceive_live(
        self,
        output_dir: str = ".",
        capture_ui_structure: bool = True,
        context: PerceptionContext | None = None,
    ) -> ScreenObservation:
        """Live mode: capture from connected device and analyse.

        Args:
            output_dir: Directory to save the captured screenshot.
            capture_ui_structure: Whether to also capture the UI hierarchy.
            context: Optional perception context.

        Returns:
            A ScreenObservation with the structured analysis.

        Raises:
            RuntimeError: If no device adapter is configured.
        """
        if self.device is None:
            raise RuntimeError(
                "Live perception requires a device adapter. "
                "Use perceive_screenshot() for offline mode."
            )

        # Capture screenshot
        output_path = str(Path(output_dir) / "screenshot.png")
        screenshot_path = await self.device.get_screenshot(output_path)
        logger.info("Captured screenshot: %s", screenshot_path)

        # Optionally capture UI structure
        ui_xml = None
        if capture_ui_structure:
            ui_xml = await self.device.get_ui_structure()
            if ui_xml:
                logger.info("Captured UI structure (%d chars)", len(ui_xml))
            else:
                logger.info("UI structure not available")

        # Analyse
        return await self.perceive_screenshot(
            screenshot_path=screenshot_path,
            ui_structure_xml=ui_xml,
            context=context,
        )

    @staticmethod
    def observation_to_json(observation: ScreenObservation, indent: int = 2) -> str:
        """Serialise a ScreenObservation to JSON string."""
        return observation.model_dump_json(indent=indent)

    @staticmethod
    def observation_from_json(json_str: str) -> ScreenObservation:
        """Deserialise a ScreenObservation from JSON string."""
        return ScreenObservation.model_validate_json(json_str)

    @staticmethod
    def save_observation(observation: ScreenObservation, path: str) -> None:
        """Save a ScreenObservation to a JSON file."""
        Path(path).write_text(
            observation.model_dump_json(indent=2)
        )
        logger.info("Observation saved to %s", path)

    @staticmethod
    def load_observation(path: str) -> ScreenObservation:
        """Load a ScreenObservation from a JSON file."""
        return ScreenObservation.model_validate_json(
            Path(path).read_text()
        )
