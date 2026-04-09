"""Device adapter protocol — thin interface for interacting with the target device."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pathfinder.contracts.common import AppInfo, DeviceInfo
from pathfinder.contracts.app_reference import AppReference
from pathfinder.device.actions import DeviceAction


class ActionResult:
    """Result of a device action."""

    def __init__(self, success: bool, message: str = "", duration_ms: int = 0):
        self.success = success
        self.message = message
        self.duration_ms = duration_ms


@runtime_checkable
class DeviceAdapter(Protocol):
    """Thin interface for interacting with the target device/app.

    Implementations wrap platform-specific automation (ADB, XCTest, etc.)
    behind this common interface. No intelligence lives here — it's purely
    a mechanism for executing physical interactions and capturing state."""

    async def get_screenshot(self, output_path: str) -> str:
        """Capture the current screen and save to output_path.
        Returns the path to the saved screenshot."""
        ...

    async def get_ui_structure(self) -> str | None:
        """Get the accessibility tree / view hierarchy as XML string.
        Returns None if not supported or extraction fails."""
        ...

    async def perform_action(self, action: DeviceAction) -> ActionResult:
        """Execute a physical interaction on the device."""
        ...

    async def get_app_info(self) -> AppInfo:
        """Get metadata about the currently running foreground app."""
        ...

    async def install_app(self, reference: AppReference) -> None:
        """Install the app from a bundle path or store reference."""
        ...

    async def launch_app(self, package: str) -> None:
        """Launch or restart the specified app."""
        ...

    async def reset_app_state(self, package: str) -> None:
        """Clear app data and return to a clean state."""
        ...

    async def get_device_info(self) -> DeviceInfo:
        """Get device metadata (OS version, screen size, etc.)."""
        ...
