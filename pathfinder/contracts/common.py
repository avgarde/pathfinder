"""Common types shared across all contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Provenance(str, Enum):
    """How the system came to believe something."""

    OBSERVED = "observed"
    INFERRED_STRUCTURE = "inferred_structure"
    INFERRED_CATEGORY = "inferred_category"
    EXTERNALLY_SUPPLIED = "externally_supplied"
    AUTOMATED_DISCOVERY = "automated_discovery"


class ScreenType(str, Enum):
    """High-level classification of a screen's purpose."""

    LOGIN = "login"
    REGISTRATION = "registration"
    HOME = "home"
    LIST = "list"
    DETAIL = "detail"
    FORM = "form"
    SETTINGS = "settings"
    SEARCH = "search"
    NAVIGATION = "navigation"
    CONFIRMATION = "confirmation"
    ERROR = "error"
    LOADING = "loading"
    ONBOARDING = "onboarding"
    MODAL = "modal"
    UNKNOWN = "unknown"


class FlowCategory(str, Enum):
    """Classification of a flow's nature."""

    CORE = "core"
    SECONDARY = "secondary"
    SETTINGS = "settings"
    ONBOARDING = "onboarding"
    ERROR_RECOVERY = "error_recovery"
    EDGE_CASE = "edge_case"
    AUTHENTICATION = "authentication"


class ElementReference(BaseModel):
    """Identifies a UI element. Multiple identification strategies
    are stored; the adapter tries them in order of reliability."""

    resource_id: str | None = None
    text: str | None = None
    content_description: str | None = None
    class_name: str | None = None
    bounds: tuple[int, int, int, int] | None = None  # left, top, right, bottom
    description: str = ""  # Semantic description


class Coordinates(BaseModel):
    """Screen coordinates."""

    x: int
    y: int


class DeviceInfo(BaseModel):
    """Device metadata."""

    platform: str  # "android", "ios", "web"
    os_version: str
    device_name: str
    screen_width: int
    screen_height: int
    screen_density: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class AppInfo(BaseModel):
    """Metadata about the currently running app."""

    package_name: str | None = None
    app_name: str | None = None
    version: str | None = None
    activity: str | None = None  # Current activity (Android)
    extra: dict[str, Any] = Field(default_factory=dict)
