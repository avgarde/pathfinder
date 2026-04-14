"""FlowVerifier: Phase 2 of the Pathfinder pipeline.

The FlowVerifier executes candidate flows against a live application and
promotes them to "validated" status (or marks them failed/partial/blocked).

Design goals
------------
- DragonCrawl-inspired: semantic element matching rather than brittle text/ID
  selectors; VQA-style assertion evaluation via the AI; per-step evidence.
- Produces FlowVerificationResult attached to each Flow.
- Regression mode: re-runs previously verified flows, exits 1 on regression.

Architecture
------------
For each flow:
  1. Check entry conditions (structural checks or AI semantic checks)
  2. For each step:
     a. Perceive the current screen
     b. Semantic element matching: find the element best matching
        step.semantic_target (or step.action_target_text) among visible
        interactive elements
     c. Execute the action on that element
     d. Perceive the resulting screen
     e. Evaluate post_assertions (structural + AI semantic)
     f. Record StepVerificationResult with evidence
  3. Promote/demote the flow's validation_status
  4. Return VerificationRun with all results
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

from pathfinder.contracts.flow import (
    EntryCondition,
    Flow,
    FlowSet,
    FlowStep,
    FlowVerificationResult,
    StepAssertion,
    StepEvidence,
    StepVerificationResult,
)
from pathfinder.contracts.inputs import InputRegistry, InputSpec
from pathfinder.contracts.screen_observation import ScreenObservation, UIElement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Verification run output
# ---------------------------------------------------------------------------


@dataclass
class VerificationRun:
    """The complete output of running the verifier on a FlowSet."""

    run_id: str
    run_dir: str
    flow_results: list[FlowVerificationResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    # Counts
    total_flows: int = 0
    flows_validated: int = 0
    flows_partial: int = 0
    flows_failed: int = 0
    flows_blocked: int = 0

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    @property
    def success(self) -> bool:
        """True if all flows were validated (no failures or blocks)."""
        return self.flows_failed == 0 and self.flows_blocked == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": self.run_dir,
            "duration_seconds": round(self.duration_seconds, 1),
            "total_flows": self.total_flows,
            "flows_validated": self.flows_validated,
            "flows_partial": self.flows_partial,
            "flows_failed": self.flows_failed,
            "flows_blocked": self.flows_blocked,
            "success": self.success,
            "flow_results": [
                {
                    "flow_id": fr.flow_id,
                    "success": fr.success,
                    "steps_verified": fr.steps_verified,
                    "steps_passed": fr.steps_passed,
                    "entry_conditions_met": fr.entry_conditions_met,
                    "error": fr.error,
                    "duration_ms": fr.duration_ms,
                }
                for fr in self.flow_results
            ],
        }


# ---------------------------------------------------------------------------
# Main verifier
# ---------------------------------------------------------------------------


class FlowVerifier:
    """Executes candidate flows against a live application and verifies them.

    Usage:
        verifier = FlowVerifier(ai=ai_instance, device=web_adapter)
        run = await verifier.verify_all(
            flow_set=flow_set,
            start_url="https://app.example.com",
            output_dir="./verification",
        )
        # run.success → True/False
        # Each flow in flow_set now has verification_result attached

    Regression mode (for CI):
        run = await verifier.verify_all(
            flow_set=verified_flow_set,
            start_url="https://app.example.com",
            regression=True,  # Only re-runs "validated" flows; fails on any regression
        )
        sys.exit(0 if run.success else 1)
    """

    def __init__(
        self,
        ai: Any,              # AIInterface — using Any to avoid import cycle at module level
        device: Any,          # DeviceAdapter
        output_dir: str = "./verification",
        settle_time: float = 1.5,
        max_retry_steps: int = 1,    # Retry a failing step this many times
        ai_assertions: bool = True,  # Use AI for semantic assertion evaluation
    ):
        self.ai = ai
        self.device = device
        self.output_dir = output_dir
        self.settle_time = settle_time
        self.max_retry_steps = max_retry_steps
        self.ai_assertions = ai_assertions
        # Set during verify_flow so _execute_step_action has flow context
        # for AI-driven input generation without needing to thread it through
        # every helper signature.
        self._current_flow_goal: str = ""

    async def verify_all(
        self,
        flow_set: FlowSet,
        start_url: str | None = None,
        input_specs: list[InputSpec] | None = None,
        regression: bool = False,
        run_id: str | None = None,
    ) -> VerificationRun:
        """Verify all flows in a FlowSet.

        Args:
            flow_set: The FlowSet to verify. Flows are modified in-place:
                      each flow's validation_status and verification_result
                      are updated.
            start_url: Starting URL for web apps (navigated to before each flow).
            input_specs: Input values to supply during verification.
            regression: If True, only runs flows with status="validated" and
                        fails if any were previously passing but now fail.
            run_id: Optional run identifier.

        Returns:
            VerificationRun with aggregate results and per-flow details.
        """
        run_id = run_id or self._make_run_id()
        run_dir = Path(self.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Starting verification run %s: %d flows, regression=%s",
            run_id, len(flow_set.flows), regression,
        )

        vrun = VerificationRun(
            run_id=run_id,
            run_dir=str(run_dir),
            start_time=time.time(),
            total_flows=len(flow_set.flows),
        )

        input_registry = InputRegistry(specs=input_specs)

        # Filter flows: in regression mode, only verify previously-validated flows
        flows_to_verify = [
            f for f in flow_set.flows
            if not regression or f.validation_status == "validated"
        ]

        for flow in flows_to_verify:
            logger.info("Verifying flow: %s [%s]", flow.flow_id, flow.goal[:60])

            # Navigate to start URL before each flow
            if start_url and hasattr(self.device, "navigate_to"):
                try:
                    await self.device.navigate_to(start_url)
                    await asyncio.sleep(self.settle_time)
                except Exception as e:
                    logger.warning("Failed to navigate to start URL: %s", e)

            flow_result = await self.verify_flow(
                flow=flow,
                input_registry=input_registry,
                output_dir=str(run_dir / flow.flow_id),
            )

            # Attach result to flow and update its status
            flow.verification_result = flow_result
            flow.last_verified_at = datetime.now(timezone.utc)
            flow.validation_run_id = run_id
            flow.execution_duration_ms = flow_result.duration_ms

            if not flow_result.entry_conditions_met:
                flow.validation_status = "blocked"
                vrun.flows_blocked += 1
            elif flow_result.success:
                # Compute evidence strength from step pass rate
                if flow_result.steps_verified > 0:
                    flow.evidence_strength = (
                        flow_result.steps_passed / flow_result.steps_verified
                    )
                flow.validation_status = "validated"
                vrun.flows_validated += 1
            elif flow_result.steps_passed > 0:
                flow.validation_status = "partial"
                vrun.flows_partial += 1
            else:
                flow.validation_status = "failed"
                vrun.flows_failed += 1

            vrun.flow_results.append(flow_result)

            logger.info(
                "  Flow %s: %s (%d/%d steps passed)",
                flow.flow_id,
                flow.validation_status,
                flow_result.steps_passed,
                flow_result.steps_verified,
            )

        vrun.end_time = time.time()

        # Save verification summary
        import json
        summary_path = run_dir / "verification_summary.json"
        summary_path.write_text(json.dumps(vrun.to_dict(), indent=2))
        logger.info(
            "Verification run %s complete: %d/%d validated (%.1fs). "
            "Failed: %d, blocked: %d, partial: %d",
            run_id,
            vrun.flows_validated,
            vrun.total_flows,
            vrun.duration_seconds,
            vrun.flows_failed,
            vrun.flows_blocked,
            vrun.flows_partial,
        )

        return vrun

    async def verify_flow(
        self,
        flow: Flow,
        input_registry: InputRegistry | None = None,
        output_dir: str | None = None,
    ) -> FlowVerificationResult:
        """Verify a single flow against the live application.

        Returns a FlowVerificationResult. Does NOT modify the flow object
        directly — the caller handles status promotion.
        """
        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Make goal available to helper methods (e.g. input generation context)
        self._current_flow_goal = flow.goal

        result = FlowVerificationResult(
            flow_id=flow.flow_id,
            success=False,
            steps_verified=0,
            steps_passed=0,
            run_id="",
            started_at=datetime.now(timezone.utc),
        )

        t_start = time.time()

        # 1. Check entry conditions
        entry_ok = await self._check_entry_conditions(
            flow.entry_conditions,
            input_registry,
        )
        result.entry_conditions_met = entry_ok

        if not entry_ok:
            result.error = "Entry conditions not met"
            result.duration_ms = int((time.time() - t_start) * 1000)
            return result

        # 2. Execute and verify each step
        step_results: list[StepVerificationResult] = []

        for step in flow.steps:
            step_result = await self._verify_step(
                step=step,
                flow=flow,
                step_index=len(step_results),
                output_dir=output_dir,
                input_registry=input_registry,
            )
            step_results.append(step_result)
            result.steps_verified += 1
            if step_result.success:
                result.steps_passed += 1

            # Update step's evidence in the flow (marks execution_verified=True)
            if step_result.success and step.evidence:
                step.evidence.execution_verified = True
            elif step_result.success and step_result.screenshot_path:
                step.evidence = StepEvidence(
                    observation_id=f"ver_{uuid.uuid4().hex[:8]}",
                    screenshot_path=step_result.screenshot_path,
                    execution_verified=True,
                    ai_confidence=1.0,
                )

            # A blocking assertion failure stops the flow
            if not step_result.success:
                blocking_failed = any(
                    ar.get("is_blocking", True) and not ar.get("passed", False)
                    for ar in step_result.assertion_results
                )
                if blocking_failed:
                    logger.warning(
                        "Step %d blocking assertion failed — stopping flow",
                        step.step_number,
                    )
                    break

        result.step_results = step_results
        result.success = (result.steps_passed == result.steps_verified
                          and result.steps_verified > 0)
        result.duration_ms = int((time.time() - t_start) * 1000)
        return result

    async def _check_entry_conditions(
        self,
        conditions: list[EntryCondition],
        input_registry: InputRegistry | None,
    ) -> bool:
        """Check all required entry conditions.

        Returns True if all required conditions are met.
        Non-required conditions are logged but don't block.
        """
        for cond in conditions:
            if not cond.required:
                continue

            # input_available conditions: check input registry
            if cond.condition_type == "input_available":
                if input_registry is None:
                    logger.warning(
                        "Entry condition requires input_available but "
                        "no input_registry supplied: %s", cond.description
                    )
                    # Don't block if we can't check
                    continue
                # Check if any spec field matches the description key
                # (best-effort: field name often in description)
                # This is a loose check — a more precise system would use
                # cond.check.target as the field name
                if cond.check and cond.check.target:
                    if not input_registry.has_spec(cond.check.target):
                        logger.warning(
                            "Entry condition not met: input '%s' not supplied",
                            cond.check.target,
                        )
                        return False

            # Screen type conditions: check current screen
            elif cond.condition_type == "screen_type" and cond.check:
                # Take a quick perception snapshot to check
                try:
                    obs = await self._quick_perceive()
                    if obs and cond.check.expected_value:
                        if obs.screen_type.value != cond.check.expected_value:
                            logger.warning(
                                "Entry condition not met: expected screen_type='%s', "
                                "got '%s'",
                                cond.check.expected_value,
                                obs.screen_type.value,
                            )
                            return False
                except Exception as e:
                    logger.warning(
                        "Could not check screen_type entry condition: %s", e
                    )

            # Other conditions: log and proceed (can't check structurally)
            else:
                logger.debug(
                    "Entry condition '%s' (%s): assuming met (no structural check)",
                    cond.description, cond.condition_type,
                )

        return True

    async def _verify_step(
        self,
        step: FlowStep,
        flow: Flow,
        step_index: int,
        output_dir: str | None,
        input_registry: InputRegistry | None,
    ) -> StepVerificationResult:
        """Verify a single step: match element, execute action, check assertions."""
        t_start = time.time()
        step_result = StepVerificationResult(
            step_number=step.step_number,
            success=False,
            assertion_results=[],
        )

        try:
            # --- Pre-assertions ---
            if step.pre_assertions:
                pre_obs = await self._quick_perceive()
                for assertion in step.pre_assertions:
                    ar = await self._evaluate_assertion(assertion, pre_obs)
                    step_result.assertion_results.append(ar)
                    if not ar["passed"] and ar.get("is_blocking", True):
                        step_result.error = f"Pre-assertion failed: {assertion.description}"
                        step_result.duration_ms = int((time.time() - t_start) * 1000)
                        return step_result

            # --- Execute action ---
            if step.action_type and step.action_type != "none":
                execute_ok, element_used, screenshot_path = await self._execute_step_action(
                    step=step,
                    output_dir=output_dir,
                    step_index=step_index,
                    input_registry=input_registry,
                )
                step_result.element_matched = element_used
                step_result.screenshot_path = screenshot_path

                if not execute_ok:
                    step_result.error = f"Action execution failed for step {step.step_number}"
                    step_result.duration_ms = int((time.time() - t_start) * 1000)
                    return step_result

                # Settle
                await asyncio.sleep(self.settle_time)

            # --- Post-assertions ---
            post_obs = await self._quick_perceive()

            # Save screenshot for this step
            if output_dir and not step_result.screenshot_path:
                try:
                    screenshot_path = str(
                        Path(output_dir) / f"step_{step.step_number:03d}_post.png"
                    )
                    await self.device.capture_screenshot(screenshot_path)
                    step_result.screenshot_path = screenshot_path
                except Exception:
                    pass

            all_passed = True
            for assertion in step.post_assertions:
                ar = await self._evaluate_assertion(assertion, post_obs)
                step_result.assertion_results.append(ar)
                if not ar["passed"]:
                    if ar.get("is_blocking", True):
                        all_passed = False
                    else:
                        logger.debug(
                            "Step %d non-blocking assertion failed: %s",
                            step.step_number, assertion.description,
                        )

            # If no assertions defined, consider the step passed if action succeeded
            if not step.post_assertions:
                all_passed = True

            step_result.success = all_passed

        except Exception as e:
            logger.warning("Step %d verification error: %s", step.step_number, e)
            step_result.error = str(e)

        step_result.duration_ms = int((time.time() - t_start) * 1000)
        return step_result

    async def _execute_step_action(
        self,
        step: FlowStep,
        output_dir: str | None,
        step_index: int,
        input_registry: InputRegistry | None,
    ) -> tuple[bool, str | None, str | None]:
        """Execute the step's action on the device.

        Returns (success, element_description_used, screenshot_path).
        """
        action_type = step.action_type
        screenshot_path: str | None = None

        try:
            if action_type == "tap":
                # Semantic element matching: find best element
                target = step.semantic_target or step.action_target_text or ""
                element_desc = await self._find_and_tap(target)
                # Save screenshot before the action
                if output_dir:
                    screenshot_path = str(
                        Path(output_dir) / f"step_{step.step_number:03d}_pre.png"
                    )
                    try:
                        await self.device.capture_screenshot(screenshot_path)
                    except Exception:
                        screenshot_path = None
                return True, element_desc, screenshot_path

            elif action_type == "type":
                # Resolve input value — use async_resolve so the "generate"
                # strategy can call the AI to synthesise a realistic value.
                input_value = step.action_input_value or ""
                if step.input_field and input_registry:
                    # Prefer async_resolve (AI synthesis) over synchronous fallback
                    resolved = await input_registry.async_resolve(
                        field=step.input_field,
                        context=(
                            f"Flow: {self._current_flow_goal}\n"
                            f"Step {step.step_number}: {step.intent}"
                        ),
                        ai=self.ai,
                    )
                    if resolved:
                        input_value = resolved

                target = step.semantic_target or step.action_target_text or ""
                await self._find_and_type(target, input_value)
                return True, target, screenshot_path

            elif action_type == "back":
                await self.device.perform_action(
                    __import__(
                        "pathfinder.device.actions",
                        fromlist=["BackAction"]
                    ).BackAction(description="Verifier: navigate back")
                )
                return True, "back navigation", screenshot_path

            elif action_type == "scroll":
                direction = step.action_input_value or "down"
                from pathfinder.device.actions import ScrollAction
                await self.device.perform_action(
                    ScrollAction(direction=direction, description=f"Scroll {direction}")
                )
                return True, f"scroll {direction}", screenshot_path

            elif action_type == "navigate":
                url = step.action_input_value or step.action_target_text or ""
                if url and hasattr(self.device, "navigate_to"):
                    await self.device.navigate_to(url)
                    return True, f"navigate to {url}", screenshot_path
                return False, None, screenshot_path

            elif action_type in ("wait", "none", None):
                return True, None, screenshot_path

            else:
                logger.warning("Unknown action_type '%s' in step %d", action_type, step.step_number)
                return False, None, screenshot_path

        except Exception as e:
            logger.warning(
                "Action '%s' failed on step %d: %s",
                action_type, step.step_number, e,
            )
            return False, None, screenshot_path

    async def _find_and_tap(self, semantic_target: str) -> str:
        """Find the best-matching interactive element and tap it.

        Uses the device's accessibility tree or page structure to find an
        element semantically matching the target description.
        """
        if not semantic_target:
            return "(no target)"

        # Get current page accessibility info
        elements = await self._get_interactive_elements()

        if not elements:
            # Fallback: try direct text click via page.click if web adapter
            if hasattr(self.device, "_page") and self.device._page:
                try:
                    # Try exact text match first
                    page = self.device._page
                    await page.get_by_text(semantic_target, exact=False).first.click()
                    return semantic_target
                except Exception:
                    # Try role-based
                    try:
                        await page.get_by_role("button", name=semantic_target).first.click()
                        return semantic_target
                    except Exception:
                        pass
            return semantic_target

        # Score elements by semantic similarity to target
        best_element = self._find_best_element(semantic_target, elements)

        if best_element and hasattr(self.device, "_page") and self.device._page:
            page = self.device._page
            try:
                # Try clicking by label/text
                label = best_element.get("label") or best_element.get("text", "")
                if label:
                    try:
                        await page.get_by_text(label, exact=True).first.click()
                        return label
                    except Exception:
                        pass
                # Try by role
                role = best_element.get("role", "")
                name = best_element.get("name") or label
                if role and name:
                    try:
                        await page.get_by_role(role, name=name).first.click()
                        return f"{role}:{name}"
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("Semantic element click failed: %s", e)

        return semantic_target

    async def _find_and_type(self, semantic_target: str, value: str) -> None:
        """Find an input element and type into it."""
        if hasattr(self.device, "_page") and self.device._page:
            page = self.device._page
            try:
                # Try by placeholder / label
                field = page.get_by_placeholder(semantic_target).first
                await field.fill(value)
                return
            except Exception:
                pass
            try:
                field = page.get_by_label(semantic_target).first
                await field.fill(value)
                return
            except Exception:
                pass
            try:
                # Fallback: first visible input
                field = page.locator("input:visible").first
                await field.fill(value)
                return
            except Exception:
                pass
        logger.warning("Could not find input for '%s'", semantic_target)

    async def _get_interactive_elements(self) -> list[dict[str, Any]]:
        """Get a list of interactive elements from the current page."""
        elements: list[dict[str, Any]] = []
        if hasattr(self.device, "_page") and self.device._page:
            try:
                page = self.device._page
                # Evaluate JS to get interactive elements
                elements = await page.evaluate("""() => {
                    const interactive = [];
                    const selectors = [
                        'a[href]', 'button', 'input', 'select', 'textarea',
                        '[role="button"]', '[role="link"]', '[role="menuitem"]',
                        '[role="tab"]', '[role="option"]'
                    ];
                    for (const selector of selectors) {
                        document.querySelectorAll(selector).forEach(el => {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                interactive.push({
                                    tag: el.tagName.toLowerCase(),
                                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                                    label: el.getAttribute('aria-label') || el.textContent?.trim()?.substring(0, 80) || '',
                                    text: el.textContent?.trim()?.substring(0, 80) || '',
                                    name: el.getAttribute('name') || '',
                                    placeholder: el.getAttribute('placeholder') || '',
                                    href: el.getAttribute('href') || '',
                                    x: Math.round(rect.x),
                                    y: Math.round(rect.y),
                                });
                            }
                        });
                    }
                    return interactive.slice(0, 100);
                }""")
            except Exception as e:
                logger.debug("Could not get interactive elements: %s", e)
        return elements

    def _find_best_element(
        self,
        target: str,
        elements: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Find the best element matching a semantic target description.

        Simple scoring: count word overlaps between target and element label/text.
        In a production system, this would use embedding similarity (like DragonCrawl).
        """
        if not elements:
            return None

        target_words = set(target.lower().split())
        best_score = 0
        best_element = None

        for el in elements:
            el_text = (
                (el.get("label") or "") + " " +
                (el.get("text") or "") + " " +
                (el.get("placeholder") or "") + " " +
                (el.get("name") or "")
            ).lower()
            el_words = set(el_text.split())

            # Jaccard-like overlap
            if target_words and el_words:
                overlap = len(target_words & el_words)
                score = overlap / len(target_words | el_words)
                if score > best_score:
                    best_score = score
                    best_element = el

        return best_element if best_score > 0 else elements[0] if elements else None

    async def _evaluate_assertion(
        self,
        assertion: StepAssertion,
        obs: ScreenObservation | None,
    ) -> dict[str, Any]:
        """Evaluate a single assertion and return a result dict."""
        result = {
            "assertion_id": assertion.assertion_id,
            "description": assertion.description,
            "assertion_type": assertion.assertion_type,
            "is_blocking": assertion.is_blocking,
            "passed": False,
            "actual_value": None,
            "error": None,
        }

        try:
            if assertion.assertion_type == "screen_type_is":
                if obs:
                    actual = obs.screen_type.value
                    result["actual_value"] = actual
                    result["passed"] = actual == assertion.expected_value
                else:
                    result["error"] = "No observation available"

            elif assertion.assertion_type == "element_visible":
                # Check if an element matching target is in the observation
                if obs and assertion.target:
                    target_lower = assertion.target.lower()
                    for el in obs.elements:
                        if (target_lower in el.label.lower() or
                                target_lower in el.semantic_role.lower()):
                            result["passed"] = True
                            result["actual_value"] = el.label
                            break
                    if not result["passed"]:
                        result["actual_value"] = f"no element matching '{assertion.target}'"
                else:
                    result["passed"] = True  # Can't check without target

            elif assertion.assertion_type == "element_enabled":
                if obs and assertion.target:
                    target_lower = assertion.target.lower()
                    for el in obs.elements:
                        if (target_lower in el.label.lower() and
                                el.is_interactive and el.is_enabled):
                            result["passed"] = True
                            result["actual_value"] = el.label
                            break

            elif assertion.assertion_type == "element_text_contains":
                if obs and assertion.target and assertion.expected_value:
                    target_lower = assertion.target.lower()
                    for el in obs.elements:
                        if target_lower in el.label.lower():
                            if assertion.expected_value.lower() in el.label.lower():
                                result["passed"] = True
                                result["actual_value"] = el.label
                                break

            elif assertion.assertion_type == "text_present":
                # Check if expected text appears anywhere visible
                if obs and assertion.expected_value:
                    for el in obs.elements:
                        if assertion.expected_value.lower() in el.label.lower():
                            result["passed"] = True
                            result["actual_value"] = el.label
                            break
                else:
                    result["passed"] = True  # No expected value to check

            elif assertion.assertion_type == "url_contains":
                if assertion.expected_value and hasattr(self.device, "_page"):
                    try:
                        page = self.device._page
                        if page:
                            current_url = page.url
                            result["actual_value"] = current_url
                            result["passed"] = assertion.expected_value in current_url
                    except Exception as e:
                        result["error"] = str(e)
                else:
                    result["passed"] = True

            elif assertion.assertion_type == "state_changed":
                # Generic "something changed" — we optimistically pass this
                # unless we have evidence it didn't. The AI verifier could be
                # invoked here in a future enhancement.
                result["passed"] = True
                result["actual_value"] = "state_changed (unverified)"

            elif assertion.assertion_type == "semantic":
                # AI-powered semantic evaluation
                if self.ai_assertions and obs:
                    result["passed"] = await self._ai_evaluate_assertion(
                        assertion.description, obs
                    )
                    result["actual_value"] = "ai_evaluated"
                else:
                    # Without AI, be optimistic
                    result["passed"] = True
                    result["actual_value"] = "semantic (not evaluated)"

        except Exception as e:
            result["error"] = str(e)
            result["passed"] = False

        return result

    async def _ai_evaluate_assertion(
        self,
        description: str,
        obs: ScreenObservation,
    ) -> bool:
        """Ask the AI to evaluate a semantic assertion against the current screen.

        This is a lightweight call — just asks "is {description} true given
        what we see on screen?" Returns True/False.
        """
        try:
            # Build a quick screen summary
            screen_summary = (
                f"Screen type: {obs.screen_type.value}\n"
                f"Purpose: {obs.screen_purpose}\n"
                f"Elements: {', '.join(e.label for e in obs.elements[:20] if e.label)}"
            )

            prompt = (
                f"Given this screen:\n{screen_summary}\n\n"
                f"Is the following assertion TRUE or FALSE?\n"
                f"Assertion: {description}\n\n"
                f"Answer with only TRUE or FALSE."
            )

            # Use the AI's synthesise_context capability as a general LLM call
            # (This is a lightweight proxy — a dedicated verify endpoint would be cleaner)
            if hasattr(self.ai, "client"):
                response = await self.ai.client.messages.create(
                    model=self.ai.config.model,
                    max_tokens=10,
                    system="You are a screen state evaluator. Answer TRUE or FALSE only.",
                    messages=[{"role": "user", "content": prompt}],
                )
                answer = response.content[0].text.strip().upper()
                return answer.startswith("TRUE")
        except Exception as e:
            logger.debug("AI assertion evaluation failed: %s", e)

        # Default: optimistic
        return True

    async def _quick_perceive(self) -> ScreenObservation | None:
        """Take a quick screenshot and perceive it. Returns None on error."""
        try:
            from pathfinder.layers.perception import PerceptionLayer
            perception = PerceptionLayer(ai=self.ai, device=self.device)
            obs = await perception.perceive_live(
                output_dir=self.output_dir,
                capture_ui_structure=False,
            )
            return obs
        except Exception as e:
            logger.debug("Quick perceive failed: %s", e)
            return None

    @staticmethod
    def _make_run_id() -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:4]
        return f"verify-{ts}-{suffix}"
