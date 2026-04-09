"""Android device adapter — wraps ADB + uiautomator2."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

from pathfinder.contracts.common import AppInfo, Coordinates, DeviceInfo, ElementReference
from pathfinder.contracts.app_reference import AppReference
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

logger = logging.getLogger(__name__)


class AndroidDeviceAdapter:
    """DeviceAdapter implementation for Android via ADB.

    Uses ADB directly for screenshots and UI hierarchy dump.
    Uses uiautomator2 for interactions when available, falling back
    to raw ADB input commands.
    """

    def __init__(self, serial: str | None = None):
        """Initialise with optional device serial (for multi-device setups)."""
        self.serial = serial
        self._adb_prefix = ["adb"]
        if serial:
            self._adb_prefix = ["adb", "-s", serial]
        self._u2_device = None

    def _adb(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run an ADB command synchronously."""
        cmd = self._adb_prefix + list(args)
        logger.debug("ADB: %s", " ".join(cmd))
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False
        )

    async def _adb_async(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        """Run an ADB command asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._adb(*args, timeout=timeout)
        )

    async def get_screenshot(self, output_path: str) -> str:
        """Capture screenshot via ADB exec-out (fastest method)."""
        result = await self._adb_async("exec-out", "screencap", "-p")
        if result.returncode != 0:
            raise RuntimeError(
                f"Screenshot capture failed: {result.stderr.decode(errors='replace')}"
            )

        Path(output_path).write_bytes(result.stdout)
        logger.info("Screenshot saved to %s (%d bytes)", output_path, len(result.stdout))
        return output_path

    async def get_ui_structure(self) -> str | None:
        """Dump the UI hierarchy via uiautomator."""
        # Dump to device, then pull
        dump_result = await self._adb_async(
            "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"
        )
        if dump_result.returncode != 0:
            logger.warning(
                "UI dump failed: %s", dump_result.stderr.decode(errors='replace')
            )
            return None

        # Read the dump
        cat_result = await self._adb_async("shell", "cat", "/sdcard/window_dump.xml")
        if cat_result.returncode != 0:
            logger.warning("Failed to read UI dump")
            return None

        xml = cat_result.stdout.decode(errors="replace")

        # Clean up
        await self._adb_async("shell", "rm", "/sdcard/window_dump.xml")

        return xml if xml.strip() else None

    async def perform_action(self, action: DeviceAction) -> ActionResult:
        """Execute an action on the device."""
        if isinstance(action, TapAction):
            return await self._tap(action)
        elif isinstance(action, TypeAction):
            return await self._type(action)
        elif isinstance(action, SwipeAction):
            return await self._swipe(action)
        elif isinstance(action, BackAction):
            return await self._back()
        elif isinstance(action, ScrollAction):
            return await self._scroll(action)
        elif isinstance(action, WaitAction):
            return await self._wait(action)
        else:
            return ActionResult(success=False, message=f"Unknown action type: {type(action)}")

    async def _resolve_coordinates(
        self, target: ElementReference | Coordinates
    ) -> tuple[int, int]:
        """Resolve an element reference or coordinates to x, y."""
        if isinstance(target, Coordinates):
            return target.x, target.y

        # For ElementReference, use bounds center if available
        if target.bounds:
            left, top, right, bottom = target.bounds
            return (left + right) // 2, (top + bottom) // 2

        # Try to find by text or resource_id via UI structure
        # (simplified — a production version would use uiautomator2 selectors)
        if target.text or target.resource_id:
            xml = await self.get_ui_structure()
            if xml:
                coords = self._find_element_in_xml(xml, target)
                if coords:
                    return coords

        raise ValueError(
            f"Cannot resolve element to coordinates: {target.description or target}"
        )

    def _find_element_in_xml(
        self, xml: str, ref: ElementReference
    ) -> tuple[int, int] | None:
        """Simple XML parsing to find element bounds by text or resource-id."""
        import re

        # Look for bounds attribute matching our element
        search_attrs = []
        if ref.text:
            search_attrs.append(f'text="{ref.text}"')
        if ref.resource_id:
            search_attrs.append(f'resource-id="{ref.resource_id}"')

        for attr in search_attrs:
            # Find the node containing this attribute
            pattern = rf'<node[^>]*{re.escape(attr)}[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'
            match = re.search(pattern, xml)
            if match:
                left, top, right, bottom = (
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                )
                return (left + right) // 2, (top + bottom) // 2
        return None

    async def _tap(self, action: TapAction) -> ActionResult:
        x, y = await self._resolve_coordinates(action.target)
        result = await self._adb_async("shell", "input", "tap", str(x), str(y))
        return ActionResult(
            success=result.returncode == 0,
            message=f"Tapped ({x}, {y}): {action.description}",
        )

    async def _type(self, action: TypeAction) -> ActionResult:
        if action.target:
            x, y = await self._resolve_coordinates(action.target)
            await self._adb_async("shell", "input", "tap", str(x), str(y))
            await asyncio.sleep(0.3)

        # Escape special characters for ADB input
        escaped = action.text.replace(" ", "%s").replace("&", "\\&")
        result = await self._adb_async("shell", "input", "text", escaped)
        return ActionResult(
            success=result.returncode == 0,
            message=f"Typed '{action.text}': {action.description}",
        )

    async def _swipe(self, action: SwipeAction) -> ActionResult:
        # Get screen dimensions for relative swipe
        info = await self.get_device_info()
        cx, cy = info.screen_width // 2, info.screen_height // 2
        dist_x = int(info.screen_width * action.distance)
        dist_y = int(info.screen_height * action.distance)

        direction_map = {
            "up": (cx, cy + dist_y // 2, cx, cy - dist_y // 2),
            "down": (cx, cy - dist_y // 2, cx, cy + dist_y // 2),
            "left": (cx + dist_x // 2, cy, cx - dist_x // 2, cy),
            "right": (cx - dist_x // 2, cy, cx + dist_x // 2, cy),
        }
        x1, y1, x2, y2 = direction_map[action.direction]
        result = await self._adb_async(
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), "300"
        )
        return ActionResult(
            success=result.returncode == 0,
            message=f"Swiped {action.direction}: {action.description}",
        )

    async def _back(self) -> ActionResult:
        result = await self._adb_async("shell", "input", "keyevent", "KEYCODE_BACK")
        return ActionResult(success=result.returncode == 0, message="Back pressed")

    async def _scroll(self, action: ScrollAction) -> ActionResult:
        # Scroll is implemented as a short swipe
        swipe = SwipeAction(
            direction="up" if action.direction == "down" else "down",
            distance=0.3,
            description=action.description,
        )
        return await self._swipe(swipe)

    async def _wait(self, action: WaitAction) -> ActionResult:
        await asyncio.sleep(action.timeout_ms / 1000)
        return ActionResult(
            success=True,
            message=f"Waited {action.timeout_ms}ms: {action.description}",
            duration_ms=action.timeout_ms,
        )

    async def get_app_info(self) -> AppInfo:
        """Get info about the currently focused app."""
        result = await self._adb_async(
            "shell", "dumpsys", "window", "windows"
        )
        output = result.stdout.decode(errors="replace")

        package = None
        activity = None
        # Parse the mCurrentFocus or mFocusedApp line
        import re
        match = re.search(r"mCurrentFocus.*?(\S+)/(\S+)\}", output)
        if match:
            package = match.group(1)
            activity = match.group(2)

        return AppInfo(
            package_name=package,
            activity=activity,
        )

    async def install_app(self, reference: AppReference) -> None:
        """Install an APK from a local path."""
        if not reference.bundle_path:
            raise ValueError("bundle_path required for Android app installation")
        result = await self._adb_async(
            "install", "-r", reference.bundle_path, timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Install failed: {result.stderr.decode(errors='replace')}"
            )

    async def launch_app(self, package: str) -> None:
        """Launch an app by package name."""
        result = await self._adb_async(
            "shell", "monkey", "-p", package, "-c",
            "android.intent.category.LAUNCHER", "1"
        )
        if result.returncode != 0:
            raise RuntimeError(f"Launch failed: {result.stderr.decode(errors='replace')}")
        await asyncio.sleep(2)  # Wait for app to start

    async def reset_app_state(self, package: str) -> None:
        """Clear app data."""
        result = await self._adb_async("shell", "pm", "clear", package)
        if result.returncode != 0:
            raise RuntimeError(f"Clear failed: {result.stderr.decode(errors='replace')}")

    async def get_device_info(self) -> DeviceInfo:
        """Get device information via ADB."""
        # Screen size
        size_result = await self._adb_async("shell", "wm", "size")
        size_output = size_result.stdout.decode(errors="replace")
        width, height = 1080, 1920  # defaults
        import re
        size_match = re.search(r"(\d+)x(\d+)", size_output)
        if size_match:
            width, height = int(size_match.group(1)), int(size_match.group(2))

        # OS version
        version_result = await self._adb_async(
            "shell", "getprop", "ro.build.version.release"
        )
        os_version = version_result.stdout.decode(errors="replace").strip()

        # Device name
        name_result = await self._adb_async(
            "shell", "getprop", "ro.product.model"
        )
        device_name = name_result.stdout.decode(errors="replace").strip()

        # Density
        density_result = await self._adb_async("shell", "wm", "density")
        density_output = density_result.stdout.decode(errors="replace")
        density = None
        density_match = re.search(r"(\d+)", density_output)
        if density_match:
            density = float(density_match.group(1))

        return DeviceInfo(
            platform="android",
            os_version=os_version or "unknown",
            device_name=device_name or "unknown",
            screen_width=width,
            screen_height=height,
            screen_density=density,
        )
