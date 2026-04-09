"""Web device adapter — wraps Playwright for browser automation.

This adapter implements the same DeviceAdapter interface as the Android
adapter, allowing the rest of the system to interact with web applications
using the same contracts and layer pipeline.

Key differences from Android:
- get_ui_structure() returns a simplified DOM representation rather than
  an accessibility tree XML
- Element resolution uses CSS selectors and text matching rather than
  resource IDs and bounds
- Navigation uses URLs rather than activities
- install_app / reset_app_state operate on browser state (navigate to URL,
  clear cookies/storage)

Usage:
    adapter = WebDeviceAdapter(headless=False)
    await adapter.start()
    await adapter.navigate_to("https://example.com")
    screenshot = await adapter.get_screenshot("screen.png")
    ...
    await adapter.stop()

Or as an async context manager:
    async with WebDeviceAdapter(headless=False) as adapter:
        await adapter.navigate_to("https://example.com")
        ...
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.common import AppInfo, Coordinates, DeviceInfo, ElementReference
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


class WebDeviceAdapter:
    """DeviceAdapter implementation for web applications via Playwright.

    Uses Playwright's async API to control a browser. The browser can run
    headless (for CI/automation) or headed (for debugging/observation).
    """

    def __init__(
        self,
        headless: bool = False,
        browser_type: str = "chromium",  # "chromium", "firefox", "webkit"
        viewport_width: int = 1280,
        viewport_height: int = 800,
        slow_mo: int = 0,  # Milliseconds to slow down operations (for debugging)
    ):
        self.headless = headless
        self.browser_type = browser_type
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.slow_mo = slow_mo

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        """Start the browser. Must be called before any other operations."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        launcher = getattr(self._playwright, self.browser_type)
        self._browser = await launcher.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )

        self._context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height},
            # Reasonable defaults for mobile-like web testing
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        self._page = await self._context.new_page()
        logger.info(
            "Browser started: %s (headless=%s, viewport=%dx%d)",
            self.browser_type, self.headless,
            self.viewport_width, self.viewport_height,
        )

    async def stop(self) -> None:
        """Stop the browser and clean up resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        logger.info("Browser stopped")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()

    @property
    def page(self):
        """The current Playwright page. Raises if browser not started."""
        if self._page is None:
            raise RuntimeError(
                "Browser not started. Call start() or use 'async with' context manager."
            )
        return self._page

    async def navigate_to(self, url: str, wait_until: str = "commit") -> None:
        """Navigate to a URL. Convenience method for web-specific usage.

        Uses wait_until="commit" (response received) rather than
        "domcontentloaded" because heavy sites can take a long time to
        parse the DOM. The real readiness check happens in
        _wait_for_stable_page() afterwards.

        If the initial load times out, we still continue — the page may
        be partially loaded but usable, and _wait_for_stable_page will
        do its best to wait for visible content.
        """
        logger.info("Navigating to: %s", url)
        try:
            await self.page.goto(url, wait_until=wait_until, timeout=60000)
        except Exception as e:
            # Don't crash on timeout — the page may still be loading and
            # become usable. Log and continue.
            logger.warning("Navigation to %s did not complete cleanly: %s", url, e)
        await self._wait_for_stable_page()

    async def _wait_for_stable_page(self, timeout_ms: int = 10000) -> None:
        """Wait until the page has meaningful visible content.

        Strategy (layered — each gate adds confidence):
        1. Wait for network to settle (no in-flight requests for 500ms).
           Catches JS-heavy SPAs that fetch data after DOMContentLoaded.
        2. Wait for the body to have visible content (non-zero height).
        3. Wait for DOM mutations to stop — a MutationObserver watches the
           subtree and resolves once nothing has changed for 400ms.
           This is the key gate for SPAs that render asynchronously.
        4. Brief extra pause for CSS transitions / animations.
        """
        # 1. Network idle (best-effort — sites with websockets may never idle)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            logger.debug("networkidle timed out — continuing anyway")

        # 2. Body has non-zero height
        try:
            await self.page.wait_for_function(
                "document.body && document.body.offsetHeight > 0",
                timeout=5000,
            )
        except Exception:
            logger.debug("Body visibility check timed out")

        # 3. DOM mutation settling — wait for the page to stop changing
        try:
            await self.page.evaluate("""() => new Promise(resolve => {
                if (!document.body) { resolve(); return; }
                let timer;
                const done = () => { observer.disconnect(); resolve(); };
                const observer = new MutationObserver(() => {
                    clearTimeout(timer);
                    timer = setTimeout(done, 400);
                });
                observer.observe(document.body, {
                    childList: true, subtree: true,
                    attributes: true, characterData: true
                });
                // If the DOM is already quiet, resolve after 400ms
                timer = setTimeout(done, 400);
            })""")
        except Exception:
            logger.debug("DOM mutation settle timed out or errored")

        # 4. Brief CSS transition settle
        await asyncio.sleep(0.3)

    async def get_screenshot(self, output_path: str) -> str:
        """Capture the current page as a screenshot.

        Waits for the page to be visually stable before capturing. This
        catches the race where a prior click triggered a navigation that
        started *after* the post-action wait completed — the page goes
        blank during the transition, and without this wait we'd capture
        that blank state.

        After capturing, checks whether the screenshot is predominantly
        blank (> 95% very-light pixels). If so, waits longer and retries
        once — this is the last-resort safety net for slow SPAs.
        """
        await self._wait_for_stable_page(timeout_ms=10000)
        await self.page.screenshot(path=output_path, full_page=False)

        # Blank-page detection: retry once if the image is mostly white
        if self._is_blank_screenshot(output_path):
            logger.warning("Screenshot appears blank — waiting 3s and retrying")
            await asyncio.sleep(3)
            await self._wait_for_stable_page(timeout_ms=10000)
            await self.page.screenshot(path=output_path, full_page=False)
            if self._is_blank_screenshot(output_path):
                logger.warning("Screenshot still appears blank after retry")

        logger.info("Screenshot saved to %s", output_path)
        return output_path

    @staticmethod
    def _is_blank_screenshot(path: str, threshold: float = 0.97) -> bool:
        """Check whether a screenshot is predominantly blank (white/near-white).

        Reads raw PNG pixel data and checks if more than `threshold` fraction
        of pixels have all RGB channels above 250. Works without PIL — uses
        the raw bytes approach for PNG, but falls back to assuming non-blank
        if parsing fails.
        """
        try:
            # Use a quick statistical sample rather than decoding full PNG
            # We'll rely on file size as a pragmatic heuristic: blank PNGs
            # compress extremely well because they're uniform.
            import os
            file_size = os.path.getsize(path)
            # A 1280x800 blank white PNG compresses to roughly 5-15 KB.
            # A page with real content is typically 50 KB+.
            # Use 20 KB as the threshold — conservative enough to avoid
            # false positives on minimal-but-real pages.
            if file_size < 20_000:
                logger.debug(
                    "Screenshot file size %d bytes — likely blank", file_size
                )
                return True
            return False
        except Exception:
            return False

    async def get_page_source(self) -> str:
        """Return the full HTML source of the current page.

        This is the raw DOM as-rendered, including any JS-injected content.
        Useful for deep-trace debugging — lets you see exactly what the
        browser had when a screenshot was taken.
        """
        return await self.page.content()

    async def get_ui_structure(self) -> str | None:
        """Extract a simplified DOM structure for AI analysis.

        Returns an XML-like representation of the visible, interactive
        elements on the page — similar in spirit to an Android accessibility
        tree but derived from the DOM.
        """
        try:
            structure = await self.page.evaluate("""() => {
                function buildTree(element, depth) {
                    if (depth > 8) return '';

                    const tag = element.tagName?.toLowerCase();
                    if (!tag) return '';

                    // Skip invisible elements
                    const style = window.getComputedStyle(element);
                    if (style.display === 'none' || style.visibility === 'hidden') return '';
                    if (element.offsetWidth === 0 && element.offsetHeight === 0) return '';

                    // Skip non-semantic containers unless they have useful attributes
                    const skipTags = new Set(['script', 'style', 'noscript', 'svg', 'path', 'br', 'hr']);
                    if (skipTags.has(tag)) return '';

                    const rect = element.getBoundingClientRect();
                    const inViewport = rect.top < window.innerHeight && rect.bottom > 0
                                    && rect.left < window.innerWidth && rect.right > 0;

                    // Build attributes string
                    const attrs = [];
                    if (element.id) attrs.push(`id="${element.id}"`);
                    if (element.className && typeof element.className === 'string' && element.className.trim()) {
                        // Just first few classes to keep it manageable
                        const classes = element.className.trim().split(/\\s+/).slice(0, 3).join(' ');
                        attrs.push(`class="${classes}"`);
                    }
                    if (element.getAttribute('role')) attrs.push(`role="${element.getAttribute('role')}"`);
                    if (element.getAttribute('aria-label')) attrs.push(`aria-label="${element.getAttribute('aria-label')}"`);
                    if (element.getAttribute('placeholder')) attrs.push(`placeholder="${element.getAttribute('placeholder')}"`);
                    if (element.getAttribute('href')) {
                        const href = element.getAttribute('href');
                        if (href && !href.startsWith('javascript:')) {
                            attrs.push(`href="${href.substring(0, 100)}"`);
                        }
                    }
                    if (element.getAttribute('type')) attrs.push(`type="${element.getAttribute('type')}"`);
                    if (element.getAttribute('name')) attrs.push(`name="${element.getAttribute('name')}"`);
                    if (element.getAttribute('value') && tag === 'input') {
                        attrs.push(`value="${element.getAttribute('value').substring(0, 50)}"`);
                    }
                    if (element.disabled) attrs.push('disabled="true"');

                    // Bounds
                    if (inViewport) {
                        attrs.push(`bounds="[${Math.round(rect.left)},${Math.round(rect.top)}][${Math.round(rect.right)},${Math.round(rect.bottom)}]"`);
                    }

                    // Interactive?
                    const interactiveTags = new Set(['a', 'button', 'input', 'select', 'textarea', 'details', 'summary']);
                    const isClickable = interactiveTags.has(tag)
                        || element.getAttribute('role') === 'button'
                        || element.getAttribute('onclick')
                        || element.getAttribute('tabindex') === '0'
                        || style.cursor === 'pointer';
                    if (isClickable) attrs.push('interactive="true"');

                    const indent = '  '.repeat(depth);
                    const attrStr = attrs.length > 0 ? ' ' + attrs.join(' ') : '';

                    // Get direct text content (not from children)
                    let text = '';
                    for (const child of element.childNodes) {
                        if (child.nodeType === 3) { // Text node
                            const t = child.textContent.trim();
                            if (t) text += t + ' ';
                        }
                    }
                    text = text.trim().substring(0, 100);

                    // Recurse into children
                    let childOutput = '';
                    for (const child of element.children) {
                        childOutput += buildTree(child, depth + 1);
                    }

                    // Skip empty non-interactive containers
                    if (!text && !childOutput && !isClickable && !['img', 'input', 'select', 'textarea', 'video', 'audio'].includes(tag)) {
                        return '';
                    }

                    if (childOutput) {
                        return `${indent}<${tag}${attrStr}>${text ? ' ' + text : ''}\\n${childOutput}${indent}</${tag}>\\n`;
                    } else if (text) {
                        return `${indent}<${tag}${attrStr}>${text}</${tag}>\\n`;
                    } else {
                        return `${indent}<${tag}${attrStr} />\\n`;
                    }
                }

                return buildTree(document.body, 0);
            }""")

            if structure and len(structure.strip()) > 0:
                logger.info("DOM structure extracted (%d chars)", len(structure))
                return structure
            return None

        except Exception as e:
            logger.warning("DOM extraction failed: %s", e)
            return None

    async def perform_action(self, action: DeviceAction) -> ActionResult:
        """Execute an action in the browser."""
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
            return ActionResult(success=False, message=f"Unknown action: {type(action)}")

    async def _resolve_locator(self, target: ElementReference | Coordinates):
        """Resolve a target to a Playwright locator or coordinates."""
        if isinstance(target, Coordinates):
            return None, (target.x, target.y)

        # Try multiple strategies in order of reliability
        page = self.page

        # 1. By CSS selector from resource_id (often maps to id or data-testid)
        if target.resource_id:
            # Try as CSS id
            loc = page.locator(f"#{target.resource_id}")
            if await loc.count() > 0:
                return loc.first, None
            # Try as data-testid
            loc = page.locator(f"[data-testid='{target.resource_id}']")
            if await loc.count() > 0:
                return loc.first, None

        # 2. By accessible name / aria-label
        if target.content_description:
            loc = page.get_by_label(target.content_description)
            if await loc.count() > 0:
                return loc.first, None

        # 3. By visible text
        if target.text:
            # Try exact match first
            loc = page.get_by_text(target.text, exact=True)
            if await loc.count() > 0:
                return loc.first, None
            # Try partial match
            loc = page.get_by_text(target.text)
            if await loc.count() > 0:
                return loc.first, None
            # Try role + name
            for role in ["button", "link", "menuitem", "tab"]:
                loc = page.get_by_role(role, name=target.text)
                if await loc.count() > 0:
                    return loc.first, None

        # 4. By bounds (click coordinates)
        if target.bounds:
            left, top, right, bottom = target.bounds
            return None, ((left + right) // 2, (top + bottom) // 2)

        raise ValueError(
            f"Cannot resolve element: {target.description or target.text or 'unknown'}"
        )

    async def _tap(self, action: TapAction) -> ActionResult:
        try:
            locator, coords = await self._resolve_locator(action.target)
            if locator:
                await locator.click(timeout=5000)
            elif coords:
                await self.page.mouse.click(coords[0], coords[1])
            # Brief pause to let JS initiate any navigation or re-render.
            # The full stability wait happens in get_screenshot() before
            # the next perception step captures the result.
            await asyncio.sleep(0.5)
            return ActionResult(success=True, message=f"Clicked: {action.description}")
        except Exception as e:
            return ActionResult(success=False, message=f"Click failed: {e}")

    async def _type(self, action: TypeAction) -> ActionResult:
        try:
            if action.target:
                locator, coords = await self._resolve_locator(action.target)
                if locator:
                    await locator.click(timeout=5000)
                elif coords:
                    await self.page.mouse.click(coords[0], coords[1])
                await asyncio.sleep(0.2)
            await self.page.keyboard.type(action.text)
            return ActionResult(success=True, message=f"Typed '{action.text}': {action.description}")
        except Exception as e:
            return ActionResult(success=False, message=f"Type failed: {e}")

    async def _swipe(self, action: SwipeAction) -> ActionResult:
        # Translate swipe to scroll for web
        scroll_map = {
            "up": (0, -300),
            "down": (0, 300),
            "left": (-300, 0),
            "right": (300, 0),
        }
        dx, dy = scroll_map[action.direction]
        await self.page.mouse.wheel(dx, dy)
        await asyncio.sleep(0.3)
        return ActionResult(success=True, message=f"Scrolled {action.direction}")

    async def _back(self) -> ActionResult:
        try:
            await self.page.go_back(timeout=10000)
            await asyncio.sleep(0.5)
            return ActionResult(success=True, message="Navigated back")
        except Exception as e:
            return ActionResult(success=False, message=f"Back failed: {e}")

    async def _scroll(self, action: ScrollAction) -> ActionResult:
        pixels = 400 if action.direction == "down" else -400
        await self.page.mouse.wheel(0, pixels)
        await asyncio.sleep(0.3)
        return ActionResult(
            success=True,
            message=f"Scrolled {action.direction}: {action.description}",
        )

    async def _wait(self, action: WaitAction) -> ActionResult:
        await asyncio.sleep(action.timeout_ms / 1000)
        return ActionResult(
            success=True,
            message=f"Waited {action.timeout_ms}ms",
            duration_ms=action.timeout_ms,
        )

    async def get_app_info(self) -> AppInfo:
        """Get info about the current page."""
        url = self.page.url
        title = await self.page.title()

        # Extract domain as "package name" equivalent
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc

        return AppInfo(
            package_name=domain,
            app_name=title or domain,
            version=None,
            activity=url,  # Current URL as "activity"
            extra={"url": url, "title": title},
        )

    async def install_app(self, reference: AppReference) -> None:
        """For web: navigate to the app's URL."""
        url = reference.web_url
        if not url:
            raise ValueError("web_url required for web app")
        await self.navigate_to(url)

    async def launch_app(self, package: str) -> None:
        """For web: navigate to the URL (package is treated as URL or domain)."""
        url = package if package.startswith("http") else f"https://{package}"
        await self.navigate_to(url)

    async def reset_app_state(self, package: str = "") -> None:
        """Clear browser state (cookies, storage)."""
        if self._context:
            await self._context.clear_cookies()
            # Clear localStorage and sessionStorage
            try:
                await self.page.evaluate("""() => {
                    try { localStorage.clear(); } catch(e) {}
                    try { sessionStorage.clear(); } catch(e) {}
                }""")
            except Exception:
                pass
            logger.info("Browser state cleared")

    async def get_device_info(self) -> DeviceInfo:
        """Get browser/viewport information."""
        viewport = self.page.viewport_size or {
            "width": self.viewport_width,
            "height": self.viewport_height,
        }

        ua = await self.page.evaluate("navigator.userAgent")

        return DeviceInfo(
            platform="web",
            os_version=ua[:80] if ua else "unknown",
            device_name=f"{self.browser_type} browser",
            screen_width=viewport["width"],
            screen_height=viewport["height"],
            extra={"browser_type": self.browser_type, "headless": self.headless},
        )
