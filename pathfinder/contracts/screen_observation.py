"""ScreenObservation: Layer 1's output — structured semantic understanding of a screen."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from pathfinder.contracts.common import ElementReference, ScreenType


class UIElement(BaseModel):
    """A single UI element with semantic understanding."""

    element_id: str
    reference: ElementReference
    element_type: str  # "button", "text_field", "link", "image", "icon", "toggle", etc.
    semantic_role: str  # What it's for: "Initiates purchase", "Navigates to cart"
    label: str  # Visible label/text
    is_interactive: bool
    is_enabled: bool = True
    is_selected: bool = False  # Whether this element is in an active/selected state
    possible_actions: list[str] = Field(default_factory=list)  # ["tap", "long_press", "type"]
    inferred_destination: str | None = None  # Where tapping might lead
    confidence: float = 1.0


class NavigationContext(BaseModel):
    """Where this screen sits in the app's navigation structure."""

    arrived_from: str | None = None  # Screen we came from
    arrival_action: str | None = None  # Action that brought us here
    visible_navigation: list[str] = Field(default_factory=list)  # Tabs, menu items, etc.
    active_navigation: str | None = None  # Which nav element is currently selected
    back_available: bool = True
    inferred_depth: int = 0  # How deep in the nav hierarchy (0 = top level)


class ScreenObservation(BaseModel):
    """Structured semantic understanding of a single app screen.
    This is the primary output of Layer 1 (Perception)."""

    # Identity & context
    observation_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    screenshot_path: str | None = None  # Path to the screenshot file
    ui_structure_path: str | None = None  # Path to accessibility tree XML

    # Semantic understanding
    screen_purpose: str  # e.g., "Product detail page for a specific item"
    screen_type: ScreenType
    app_state: dict[str, Any] = Field(default_factory=dict)

    # Elements
    elements: list[UIElement] = Field(default_factory=list)

    # Navigation
    navigation_context: NavigationContext = Field(default_factory=NavigationContext)

    # Confidence
    confidence: float = 1.0

    # Raw AI response for debugging/auditing
    raw_ai_response: str | None = None
