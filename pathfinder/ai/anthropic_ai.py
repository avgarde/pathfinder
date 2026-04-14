"""Anthropic Claude implementation of the AI interface."""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from pathlib import Path

import anthropic

from pathfinder.ai.config import AIConfig
from pathfinder.ai.interface import ExplorationPlan, PerceptionContext
from pathfinder.ai.prompts.context import (
    CONTEXT_SYNTHESIS_SYSTEM_PROMPT,
    build_context_synthesis_prompt,
)
from pathfinder.ai.prompts.exploration import (
    EXPLORATION_PLANNER_SYSTEM_PROMPT,
    build_exploration_plan_prompt,
)
from pathfinder.ai.prompts.flow_generation import (
    FLOW_GENERATION_SYSTEM_PROMPT,
    build_flow_generation_prompt,
)
from pathfinder.ai.prompts.perceive import PERCEPTION_SYSTEM_PROMPT, build_perception_prompt
from pathfinder.ai.prompts.world_model import (
    WORLD_MODEL_SYSTEM_PROMPT,
    build_world_model_update_prompt,
)
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.application_model import (
    Anomaly,
    ApplicationModel,
    ExplorationHypothesis,
    ScreenNode,
    ScreenTransition,
)
from pathfinder.contracts.common import ElementReference, Provenance, ScreenType
from pathfinder.contracts.prior_context import Capability, ContextSource, Entity, PriorContext
from pathfinder.contracts.screen_observation import (
    NavigationContext,
    ScreenObservation,
    UIElement,
)

logger = logging.getLogger(__name__)


class AnthropicAI:
    """AI interface implementation using Anthropic Claude models."""

    def __init__(self, config: AIConfig):
        self.config = config
        self.client = anthropic.AsyncAnthropic(
            api_key=config.api_key,
            base_url=config.base_url,
        )

    async def perceive(
        self,
        screenshot_path: str,
        ui_structure_xml: str | None = None,
        context: PerceptionContext | None = None,
    ) -> ScreenObservation:
        """Analyse a screenshot and produce a structured ScreenObservation."""

        # Read and encode the screenshot
        image_data = Path(screenshot_path).read_bytes()
        base64_image = base64.standard_b64encode(image_data).decode("utf-8")

        # Determine media type
        suffix = Path(screenshot_path).suffix.lower()
        media_type_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        media_type = media_type_map.get(suffix, "image/png")

        # Build the prompt
        user_prompt = build_perception_prompt(ui_structure_xml, context)

        # Call the model
        logger.info("Calling %s for perception...", self.config.model)
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=PERCEPTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                }
            ],
        )

        raw_text = response.content[0].text
        logger.debug("Raw AI response: %s", raw_text[:500])

        # Parse the response
        return self._parse_perception_response(
            raw_text, screenshot_path, ui_structure_xml
        )

    async def synthesise_context(
        self,
        app_name: str | None = None,
        package_name: str | None = None,
        raw_description: str | None = None,
        app_store_text: str | None = None,
        web_search_results: str | None = None,
        user_description: str | None = None,
        baseline: str | None = None,
        differentiation: str | None = None,
    ) -> PriorContext:
        """Synthesise gathered information into a structured PriorContext."""

        user_prompt = build_context_synthesis_prompt(
            app_name=app_name,
            package_name=package_name,
            raw_description=raw_description,
            app_store_text=app_store_text,
            web_search_results=web_search_results,
            user_description=user_description,
            baseline=baseline,
            differentiation=differentiation,
        )

        logger.info("Calling %s for context synthesis...", self.config.model)
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=CONTEXT_SYNTHESIS_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )

        raw_text = response.content[0].text
        logger.debug("Raw AI response: %s", raw_text[:500])

        return self._parse_context_response(raw_text, app_store_text, web_search_results)

    async def update_world_model(
        self,
        current_model: ApplicationModel,
        new_observations: list[ScreenObservation],
        prior_context: PriorContext | None = None,
    ) -> ApplicationModel:
        """Update the world model with new observations."""

        # Build summaries for the prompt
        current_model_summary = self._summarise_model(current_model)
        observations_summary = self._summarise_observations(new_observations)
        prior_summary = None
        if prior_context:
            prior_summary = self._summarise_prior_context(prior_context)

        user_prompt = build_world_model_update_prompt(
            current_model_summary=current_model_summary,
            new_observations_summary=observations_summary,
            prior_context_summary=prior_summary,
        )

        logger.info("Calling %s for world model update...", self.config.model)
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=WORLD_MODEL_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )

        raw_text = response.content[0].text
        logger.debug("Raw AI response: %s", raw_text[:500])

        return self._parse_world_model_response(raw_text, current_model, new_observations)

    async def plan_exploration(
        self,
        current_model: ApplicationModel,
        current_observation: ScreenObservation,
        action_history: list[str] | None = None,
        max_actions_remaining: int = 50,
        available_inputs: dict[str, str] | None = None,
        exploration_goals: list[str] | None = None,
        confirmed_goals: list[str] | None = None,
    ) -> ExplorationPlan:
        """Decide the next exploration action based on current state."""

        model_summary = self._summarise_model(current_model)
        observation_summary = self._summarise_observations([current_observation])

        user_prompt = build_exploration_plan_prompt(
            current_model_summary=model_summary,
            current_observation_summary=observation_summary,
            action_history=action_history,
            max_actions_remaining=max_actions_remaining,
            available_inputs=available_inputs,
            exploration_goals=exploration_goals,
            confirmed_goals=confirmed_goals,
        )

        logger.info("Calling %s for exploration planning...", self.config.model)
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=EXPLORATION_PLANNER_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )

        raw_text = response.content[0].text
        logger.debug("Raw exploration plan: %s", raw_text[:500])

        return self._parse_exploration_plan(raw_text)

    async def generate_flows(
        self,
        application_model: ApplicationModel,
        exploration_trace: list[dict[str, Any]],
        prior_context: PriorContext | None = None,
    ) -> list[dict[str, Any]]:
        """Identify meaningful user flows from model + trace."""

        model_summary = self._summarise_model(application_model)

        # Build a readable trace summary
        trace_lines = []
        for step in exploration_trace:
            step_num = step.get("step", "?")
            screen = step.get("screen", "unknown")
            action = step.get("action", "unknown")
            goal = step.get("goal", "")
            success = step.get("success")
            status = "" if success is None else (" [OK]" if success else " [FAILED]")
            trace_lines.append(
                f"  Step {step_num}: {action}{status}"
                f"\n    Screen: {screen}"
                + (f"\n    Goal: {goal}" if goal else "")
            )
        trace_summary = "\n".join(trace_lines) if trace_lines else "(empty trace)"

        prior_summary = None
        if prior_context:
            prior_summary = self._summarise_prior_context(prior_context)

        user_prompt = build_flow_generation_prompt(
            model_summary=model_summary,
            exploration_trace=trace_summary,
            prior_context_summary=prior_summary,
        )

        logger.info("Calling %s for flow generation...", self.config.model)
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=FLOW_GENERATION_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )

        raw_text = response.content[0].text
        logger.debug("Raw flow generation response: %s", raw_text[:500])

        return self._parse_flow_generation_response(raw_text)

    def _parse_flow_generation_response(self, raw_text: str) -> list[dict[str, Any]]:
        """Parse the AI's flow generation response into a list of flow dicts."""
        text = raw_text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse flow generation response: %s", e)
            logger.error("Raw: %s", raw_text[:1000])
            return []

        # The response should be {"flows": [...], "analysis": {...}}
        if isinstance(data, dict):
            flows = data.get("flows", [])
            analysis = data.get("analysis", {})
            if analysis:
                logger.info(
                    "Flow analysis: %d observed, %d hypothetical, "
                    "capability coverage=%.0f%%, gaps=%s",
                    analysis.get("total_observed", 0),
                    analysis.get("total_hypothetical", 0),
                    analysis.get("coverage_of_capabilities", 0) * 100,
                    analysis.get("key_gaps", []),
                )
            return flows
        elif isinstance(data, list):
            return data
        else:
            logger.error("Unexpected flow generation response type: %s", type(data))
            return []

    def _parse_exploration_plan(self, raw_text: str) -> ExplorationPlan:
        """Parse the AI's exploration plan response."""
        text = raw_text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse exploration plan: %s", e)
            logger.error("Raw: %s", raw_text[:1000])
            # Return a stop plan rather than crashing
            return ExplorationPlan(
                reasoning="Failed to parse AI response",
                should_stop=True,
                stop_reason="Parse error in exploration plan",
                action={},
                expected_outcome="",
                exploration_goal="",
            )

        return ExplorationPlan(
            reasoning=data.get("reasoning", ""),
            should_stop=data.get("should_stop", False),
            stop_reason=data.get("stop_reason"),
            action=data.get("action", {}),
            expected_outcome=data.get("expected_outcome", ""),
            exploration_goal=data.get("exploration_goal", ""),
            inputs_required=data.get("inputs_required", []),
            goals_confirmed=data.get("goals_confirmed", []),
        )

    def _summarise_model(self, model: ApplicationModel) -> str:
        """Create a concise text summary of the current model for the prompt."""
        if not model.screens and not model.capabilities:
            return "(Empty model — no screens or capabilities discovered yet)"

        parts = []
        if model.domain:
            parts.append(f"Domain: {model.domain}")
        if model.purpose:
            parts.append(f"Purpose: {model.purpose}")
        if model.baseline_category:
            parts.append(f"Category: {model.baseline_category}")
        if model.differentiators:
            parts.append(f"Differentiators: {', '.join(model.differentiators[:5])}")

        if model.entities:
            parts.append(f"\nEntities: {', '.join(e.name for e in model.entities[:15])}")

        if model.capabilities:
            parts.append("\nCapabilities:")
            for cap in model.capabilities:
                parts.append(
                    f"  - {cap.name} (importance={cap.estimated_importance:.1f}, "
                    f"freq={cap.estimated_frequency:.1f}, diff={cap.is_differentiator})"
                )

        if model.screens:
            COMPRESSION_THRESHOLD = 8
            if len(model.screens) <= COMPRESSION_THRESHOLD:
                # Small model: full details for all screens
                parts.append("\nKnown screens:")
                for s in model.screens:
                    parts.append(
                        f"  - [{s.screen_id}] {s.name} ({s.screen_type}) "
                        f"visits={s.visit_count}, participates_in=[{', '.join(s.participates_in)}]"
                    )
            else:
                # Large model: full details for recent/frontier screens only
                # Sort by visit_count descending; the most-recently active ones matter most
                recent_ids = {s.screen_id for s in sorted(model.screens, key=lambda x: x.visit_count)[-3:]}
                frontier_screen_ids = {
                    h.description.split()[-1] for h in model.frontier
                    if len(h.description.split()) > 0
                }  # rough heuristic — frontier items may reference screen IDs

                parts.append(f"\nKnown screens ({len(model.screens)} total):")
                condensed = []
                detailed = []
                for s in model.screens:
                    if s.screen_id in recent_ids:
                        detailed.append(s)
                    else:
                        condensed.append(s)

                if condensed:
                    parts.append("  [Condensed — confirmed screens]")
                    for s in condensed:
                        parts.append(
                            f"    [{s.screen_id}] {s.name} ({s.screen_type.value}) visits={s.visit_count}"
                        )
                if detailed:
                    parts.append("  [Full detail — recent screens]")
                    for s in detailed:
                        parts.append(
                            f"    [{s.screen_id}] {s.name} ({s.screen_type.value}) "
                            f"visits={s.visit_count}, participates_in=[{', '.join(s.participates_in)}]"
                        )

        if model.transitions:
            # Show at most 20 transitions (oldest ones are least useful)
            shown_transitions = model.transitions[-20:]
            parts.append(f"\nKnown transitions ({len(model.transitions)} total, showing {len(shown_transitions)}):")
            for t in shown_transitions:
                parts.append(f"  - {t.from_screen} → {t.to_screen} via \"{t.action}\"")

        if model.frontier:
            parts.append("\nExploration frontier:")
            for h in model.frontier:
                parts.append(f"  - {h.description} (priority={h.priority:.1f})")

        if model.anomalies:
            parts.append("\nAnomalies:")
            for a in model.anomalies:
                parts.append(f"  - [{a.classification}] {a.description}")

        parts.append(f"\nCoverage: {model.coverage_estimate:.0%}, Confidence: {model.confidence:.0%}")

        return "\n".join(parts)

    def _summarise_observations(self, observations: list[ScreenObservation]) -> str:
        """Create a concise text summary of observations for the prompt."""
        parts = []
        for obs in observations:
            parts.append(f"[{obs.observation_id}] Screen: {obs.screen_purpose}")
            parts.append(f"  Type: {obs.screen_type.value}")
            if obs.app_state:
                parts.append(f"  App state: {obs.app_state}")
            interactive = [e for e in obs.elements if e.is_interactive]
            if interactive:
                parts.append(f"  Interactive elements ({len(interactive)}):")
                for e in interactive:
                    selected = " [SELECTED]" if e.is_selected else ""
                    parts.append(
                        f"    - {e.label} ({e.element_type}): {e.semantic_role}{selected}"
                        + (f" → {e.inferred_destination}" if e.inferred_destination else "")
                    )
            if obs.navigation_context.visible_navigation:
                active = obs.navigation_context.active_navigation or "none"
                parts.append(
                    f"  Navigation: {obs.navigation_context.visible_navigation} "
                    f"(active: {active})"
                )
            parts.append("")
        return "\n".join(parts)

    def _summarise_prior_context(self, ctx: PriorContext) -> str:
        """Summarise prior context for the prompt."""
        parts = [f"App: {ctx.app_name} ({ctx.category or 'unknown category'})"]
        if ctx.description:
            parts.append(f"Description: {ctx.description}")
        if ctx.expected_capabilities:
            parts.append("Expected capabilities:")
            for cap in ctx.expected_capabilities:
                diff = " [DIFFERENTIATOR]" if cap.is_differentiator else ""
                parts.append(f"  - {cap.name}{diff}")
        if ctx.expected_entities:
            parts.append(f"Expected entities: {', '.join(e.name for e in ctx.expected_entities)}")
        return "\n".join(parts)

    def _parse_world_model_response(
        self,
        raw_text: str,
        current_model: ApplicationModel,
        observations: list[ScreenObservation],
    ) -> ApplicationModel:
        """Parse the AI's world model update response."""

        text = raw_text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse world model response: %s", e)
            logger.error("Raw: %s", raw_text[:1000])
            # Return current model unchanged rather than crashing
            return current_model

        # Build updated model, preserving the app_reference from current
        screens = []
        for s_data in data.get("screens", []):
            screen_type_str = s_data.get("screen_type", "unknown")
            try:
                screen_type = ScreenType(screen_type_str)
            except ValueError:
                screen_type = ScreenType.UNKNOWN

            # Find matching observation for last_observation_id
            last_obs_id = ""
            for obs in observations:
                if obs.screen_type == screen_type or obs.screen_purpose in s_data.get("purpose", ""):
                    last_obs_id = obs.observation_id
                    break

            # Compute structural fingerprint for deduplication
            name_norm = re.sub(r'[^a-z0-9]', '', s_data.get("name", "").lower())
            purpose_words = s_data.get("purpose", "").lower().split()[:5]
            fp = f"{screen_type_str}:{name_norm}:{' '.join(purpose_words)}"

            # Check if an existing screen already has this fingerprint (dedup)
            existing_by_fp = next(
                (s for s in current_model.screens if s.fingerprint == fp and fp != ""),
                None,
            )
            if existing_by_fp:
                # Merge: increment visit count on existing screen rather than adding duplicate
                visit_count = existing_by_fp.visit_count + 1
                screen_id_to_use = existing_by_fp.screen_id
            else:
                # Check if this screen existed before by screen_id (backward compatibility)
                existing = next(
                    (s for s in current_model.screens if s.screen_id == s_data.get("screen_id")),
                    None,
                )
                visit_count = (existing.visit_count + 1) if existing else 1
                screen_id_to_use = s_data.get("screen_id", f"screen_{uuid.uuid4().hex[:6]}")

            screens.append(ScreenNode(
                screen_id=screen_id_to_use,
                name=s_data.get("name", "Unknown"),
                screen_type=screen_type,
                purpose=s_data.get("purpose", ""),
                participates_in=s_data.get("participates_in", []),
                visit_count=visit_count,
                last_observation_id=last_obs_id or (existing_by_fp.last_observation_id if existing_by_fp else ""),
                fingerprint=fp,
            ))

        transitions = []
        for t_data in data.get("transitions", []):
            # Merge with existing transition counts
            existing_t = next(
                (t for t in current_model.transitions
                 if t.from_screen == t_data.get("from_screen")
                 and t.to_screen == t_data.get("to_screen")
                 and t.action == t_data.get("action")),
                None,
            )
            transitions.append(ScreenTransition(
                from_screen=t_data.get("from_screen", ""),
                to_screen=t_data.get("to_screen", ""),
                action=t_data.get("action", ""),
                observed_count=(existing_t.observed_count + 1) if existing_t else 1,
            ))

        capabilities = []
        for cap_data in data.get("capabilities", []):
            capabilities.append(Capability(
                name=cap_data.get("name", ""),
                description=cap_data.get("description", ""),
                estimated_importance=cap_data.get("estimated_importance", 0.5),
                estimated_frequency=cap_data.get("estimated_frequency", 0.5),
                is_differentiator=cap_data.get("is_differentiator", False),
                provenance=Provenance.OBSERVED if cap_data.get("status") == "confirmed"
                else Provenance.INFERRED_CATEGORY,
            ))

        entities = []
        for ent_data in data.get("entities", []):
            entities.append(Entity(
                name=ent_data.get("name", ""),
                description=ent_data.get("description", ""),
                provenance=Provenance.OBSERVED,
            ))

        frontier = []
        for f_data in data.get("frontier", []):
            frontier.append(ExplorationHypothesis(
                description=f_data.get("description", ""),
                rationale=f_data.get("rationale", ""),
                provenance=Provenance.INFERRED_STRUCTURE,
                priority=f_data.get("priority", 0.5),
                search_strategy=f_data.get("search_strategy", ""),
            ))

        anomalies = []
        for a_data in data.get("anomalies", []):
            anomalies.append(Anomaly(
                description=a_data.get("description", ""),
                observation_id=observations[0].observation_id if observations else "",
                classification=a_data.get("classification", "novel"),
                exploration_priority_boost=a_data.get("exploration_priority_boost", 0.0),
            ))

        # Build all observations list (current + new)
        all_observations = list(current_model.observations) if hasattr(current_model, 'observations') else []
        all_observations.extend(observations)

        # Compute structural coverage from graph topology (not LLM estimate)
        # confirmed = screens we've actually visited
        # total_expected = confirmed + remaining frontier items
        confirmed_count = len(screens)  # all returned screens have been visited at least once
        frontier_count = len(frontier)
        if confirmed_count + frontier_count > 0:
            structural_coverage = confirmed_count / (confirmed_count + frontier_count)
        else:
            structural_coverage = 0.0
        # Blend: use structural as the floor, LLM can push it slightly higher
        ai_coverage = float(data.get("coverage_estimate", 0.0))
        blended_coverage = max(structural_coverage, min(ai_coverage, structural_coverage + 0.15))

        return ApplicationModel(
            app_reference=current_model.app_reference,
            model_version=current_model.model_version + 1,
            domain=data.get("domain", current_model.domain),
            purpose=data.get("purpose", current_model.purpose),
            baseline_category=data.get("baseline_category", current_model.baseline_category),
            differentiators=data.get("differentiators", current_model.differentiators),
            entities=entities,
            capabilities=capabilities,
            screens=screens,
            transitions=transitions,
            frontier=frontier,
            anomalies=anomalies,
            coverage_estimate=blended_coverage,
            confidence=data.get("confidence", current_model.confidence),
        )

    def _parse_context_response(
        self,
        raw_text: str,
        app_store_text: str | None,
        web_search_results: str | None,
    ) -> PriorContext:
        """Parse the AI's JSON response into a PriorContext."""

        text = raw_text.strip()
        if text.startswith("```"):
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse context response as JSON: %s", e)
            return PriorContext(app_name=data.get("app_name", "Unknown"))

        capabilities = []
        for cap_data in data.get("expected_capabilities", []):
            capabilities.append(
                Capability(
                    name=cap_data.get("name", ""),
                    description=cap_data.get("description", ""),
                    estimated_importance=cap_data.get("estimated_importance", 0.5),
                    estimated_frequency=cap_data.get("estimated_frequency", 0.5),
                    is_differentiator=cap_data.get("is_differentiator", False),
                    provenance=Provenance.AUTOMATED_DISCOVERY,
                )
            )

        entities = []
        for ent_data in data.get("expected_entities", []):
            entities.append(
                Entity(
                    name=ent_data.get("name", ""),
                    description=ent_data.get("description", ""),
                    provenance=Provenance.AUTOMATED_DISCOVERY,
                )
            )

        sources = []
        if app_store_text:
            sources.append(ContextSource(source_type="app_store", summary="App store listing"))
        if web_search_results:
            sources.append(ContextSource(source_type="web_search", summary="Web search results"))

        return PriorContext(
            app_name=data.get("app_name", "Unknown"),
            category=data.get("category"),
            description=data.get("description", ""),
            expected_capabilities=capabilities,
            expected_entities=entities,
            sources=sources,
        )

    def _parse_perception_response(
        self,
        raw_text: str,
        screenshot_path: str,
        ui_structure_path: str | None,
    ) -> ScreenObservation:
        """Parse the AI's JSON response into a ScreenObservation."""

        # Strip markdown code fences if present
        text = raw_text.strip()
        if text.startswith("```"):
            # Remove opening fence
            first_newline = text.index("\n")
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse AI response as JSON: %s", e)
            logger.error("Raw response: %s", raw_text[:1000])
            # Return a minimal observation rather than crashing
            return ScreenObservation(
                observation_id=f"obs_{uuid.uuid4().hex[:8]}",
                screenshot_path=screenshot_path,
                screen_purpose="Failed to parse AI response",
                screen_type=ScreenType.UNKNOWN,
                confidence=0.0,
                raw_ai_response=raw_text,
            )

        # Build UIElements
        elements = []
        for i, elem_data in enumerate(data.get("elements", [])):
            elem = UIElement(
                element_id=elem_data.get("element_id", f"elem_{i:02d}"),
                reference=ElementReference(
                    text=elem_data.get("label"),
                    description=elem_data.get("bounds_description", ""),
                ),
                element_type=elem_data.get("element_type", "unknown"),
                semantic_role=elem_data.get("semantic_role", ""),
                label=elem_data.get("label", ""),
                is_interactive=elem_data.get("is_interactive", False),
                is_enabled=elem_data.get("is_enabled", True),
                is_selected=elem_data.get("is_selected", False),
                possible_actions=elem_data.get("possible_actions", []),
                inferred_destination=elem_data.get("inferred_destination"),
                confidence=elem_data.get("confidence", 0.5),
            )
            elements.append(elem)

        # Build NavigationContext
        nav_data = data.get("navigation_context", {})
        nav_context = NavigationContext(
            visible_navigation=nav_data.get("visible_navigation", []),
            active_navigation=nav_data.get("active_navigation"),
            back_available=nav_data.get("back_available", True),
            inferred_depth=nav_data.get("inferred_depth", 0),
        )

        # Build ScreenObservation
        screen_type_str = data.get("screen_type", "unknown")
        try:
            screen_type = ScreenType(screen_type_str)
        except ValueError:
            screen_type = ScreenType.UNKNOWN

        return ScreenObservation(
            observation_id=f"obs_{uuid.uuid4().hex[:8]}",
            screenshot_path=screenshot_path,
            ui_structure_path=ui_structure_path,
            screen_purpose=data.get("screen_purpose", "Unknown"),
            screen_type=screen_type,
            app_state=data.get("app_state", {}),
            elements=elements,
            navigation_context=nav_context,
            confidence=data.get("confidence", 0.5),
            raw_ai_response=raw_text,
        )
