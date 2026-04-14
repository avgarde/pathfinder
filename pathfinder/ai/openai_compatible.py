"""OpenAI-compatible implementation of the AI interface.

Works with any server that speaks the OpenAI chat completions API:
  - Ollama (http://localhost:11434/v1)
  - vLLM (http://localhost:8000/v1)
  - llama.cpp server (http://localhost:8080/v1)
  - LM Studio (http://localhost:1234/v1)
  - Together AI, Groq, OpenRouter, etc.

Uses the `openai` Python package as the client. Install with:
    pip install openai

For vision (perception), the model must support image inputs.
The image is sent as a base64 data URI in the standard OpenAI
vision format.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

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


class OpenAICompatibleAI:
    """AI interface implementation using any OpenAI-compatible API.

    This reuses all the same prompts and parsing logic as AnthropicAI,
    but sends requests via the OpenAI chat completions format.

    Usage:
        config = AIConfig(
            provider="openai_compatible",
            model="qwen2.5-vl:32b",
            base_url="http://localhost:11434/v1",
            api_key="ollama",  # ollama ignores this but the SDK requires it
        )
        ai = OpenAICompatibleAI(config)
    """

    def __init__(self, config: AIConfig):
        try:
            import openai
            from openai import NOT_GIVEN
        except ImportError:
            raise ImportError(
                "The 'openai' package is required for OpenAI-compatible backends. "
                "Install it with: pip install openai"
            )

        self.config = config
        self.client = openai.OpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url,
            timeout=config.timeout,  # seconds — covers model load + inference
        )

    def _chat(
        self,
        system: str,
        user_content: str | list[dict[str, Any]],
        temperature: float | None = None,
    ) -> str:
        """Send a chat completion request and return the text response.

        user_content can be a plain string or a list of content parts
        (for multimodal messages with images).

        Uses streaming so we can log token-by-token progress — local
        models can be slow, and without streaming there's no indication
        that anything is happening.
        """
        import time as _time

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
        ]

        if isinstance(user_content, str):
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_content})

        has_image = isinstance(user_content, list) and any(
            p.get("type") == "image_url" for p in user_content
        )
        task_hint = " (with screenshot)" if has_image else ""

        logger.info(
            "Calling %s at %s%s — this may take a few minutes for local models...",
            self.config.model, self.config.base_url, task_hint,
        )
        t0 = _time.monotonic()

        # Stream the response so we get early feedback
        chunks: list[str] = []
        token_count = 0

        try:
            stream = self.client.chat.completions.create(
                model=self.config.model,
                messages=messages,
                max_tokens=self.config.max_tokens,
                temperature=temperature if temperature is not None else self.config.temperature,
                stream=True,
            )

            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    chunks.append(delta.content)
                    token_count += 1
                    # Log progress every 100 tokens
                    if token_count % 100 == 0:
                        elapsed = _time.monotonic() - t0
                        logger.info(
                            "  ... %d tokens in %.1fs (%.1f tok/s)",
                            token_count, elapsed, token_count / elapsed if elapsed > 0 else 0,
                        )

        except Exception as e:
            # Fall back to non-streaming if the server doesn't support it
            if "stream" in str(e).lower():
                logger.info("Streaming not supported, falling back to non-streaming call...")
                response = self.client.chat.completions.create(
                    model=self.config.model,
                    messages=messages,
                    max_tokens=self.config.max_tokens,
                    temperature=temperature if temperature is not None else self.config.temperature,
                )
                text = response.choices[0].message.content or ""
                elapsed = _time.monotonic() - t0
                logger.info("Response: %d chars in %.1fs", len(text), elapsed)
                return text
            raise

        text = "".join(chunks)
        elapsed = _time.monotonic() - t0
        logger.info(
            "Response: %d tokens, %d chars in %.1fs (%.1f tok/s)",
            token_count, len(text), elapsed,
            token_count / elapsed if elapsed > 0 else 0,
        )
        logger.debug("Response text: %s", text[:300])
        return text

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        """Remove markdown code fences from a JSON response."""
        text = text.strip()
        if text.startswith("```"):
            first_nl = text.index("\n")
            text = text[first_nl + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
        return text

    # ------------------------------------------------------------------
    # Perception (Layer 1)
    # ------------------------------------------------------------------

    async def perceive(
        self,
        screenshot_path: str,
        ui_structure_xml: str | None = None,
        context: PerceptionContext | None = None,
    ) -> ScreenObservation:
        """Analyse a screenshot using an OpenAI-compatible vision model."""

        image_data = Path(screenshot_path).read_bytes()
        base64_image = base64.standard_b64encode(image_data).decode("utf-8")

        suffix = Path(screenshot_path).suffix.lower()
        media_map = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}
        media_type = media_map.get(suffix, "png")

        user_prompt = build_perception_prompt(ui_structure_xml, context)

        # OpenAI vision format: image_url with data URI
        user_content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/{media_type};base64,{base64_image}",
                },
            },
            {
                "type": "text",
                "text": user_prompt,
            },
        ]

        raw_text = self._chat(PERCEPTION_SYSTEM_PROMPT, user_content)
        return self._parse_perception_response(raw_text, screenshot_path, ui_structure_xml)

    # ------------------------------------------------------------------
    # Context synthesis (Layer 0)
    # ------------------------------------------------------------------

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

        raw_text = self._chat(CONTEXT_SYNTHESIS_SYSTEM_PROMPT, user_prompt)
        return self._parse_context_response(raw_text, app_store_text, web_search_results)

    # ------------------------------------------------------------------
    # World model update (Layer 2)
    # ------------------------------------------------------------------

    async def update_world_model(
        self,
        current_model: ApplicationModel,
        new_observations: list[ScreenObservation],
        prior_context: PriorContext | None = None,
    ) -> ApplicationModel:
        current_model_summary = self._summarise_model(current_model)
        observations_summary = self._summarise_observations(new_observations)
        prior_summary = self._summarise_prior_context(prior_context) if prior_context else None

        user_prompt = build_world_model_update_prompt(
            current_model_summary=current_model_summary,
            new_observations_summary=observations_summary,
            prior_context_summary=prior_summary,
        )

        raw_text = self._chat(WORLD_MODEL_SYSTEM_PROMPT, user_prompt)
        return self._parse_world_model_response(raw_text, current_model, new_observations)

    # ------------------------------------------------------------------
    # Exploration planning (Orchestrator)
    # ------------------------------------------------------------------

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

        raw_text = self._chat(EXPLORATION_PLANNER_SYSTEM_PROMPT, user_prompt)
        return self._parse_exploration_plan(raw_text)

    # ------------------------------------------------------------------
    # Flow generation (Layer 3)
    # ------------------------------------------------------------------

    async def generate_flows(
        self,
        application_model: ApplicationModel,
        exploration_trace: list[dict[str, Any]],
        prior_context: PriorContext | None = None,
    ) -> list[dict[str, Any]]:
        model_summary = self._summarise_model(application_model)

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

        prior_summary = self._summarise_prior_context(prior_context) if prior_context else None

        user_prompt = build_flow_generation_prompt(
            model_summary=model_summary,
            exploration_trace=trace_summary,
            prior_context_summary=prior_summary,
        )

        raw_text = self._chat(FLOW_GENERATION_SYSTEM_PROMPT, user_prompt)
        return self._parse_flow_generation_response(raw_text)

    # ------------------------------------------------------------------
    # Response parsing — mirrors AnthropicAI's parsers exactly
    # ------------------------------------------------------------------

    def _parse_exploration_plan(self, raw_text: str) -> ExplorationPlan:
        text = self._strip_json_fences(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse exploration plan: %s", e)
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

    def _parse_flow_generation_response(self, raw_text: str) -> list[dict[str, Any]]:
        text = self._strip_json_fences(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse flow generation response: %s", e)
            return []

        if isinstance(data, dict):
            return data.get("flows", [])
        elif isinstance(data, list):
            return data
        return []

    def _parse_perception_response(
        self,
        raw_text: str,
        screenshot_path: str,
        ui_structure_path: str | None,
    ) -> ScreenObservation:
        text = self._strip_json_fences(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse perception response: %s", e)
            return ScreenObservation(
                observation_id=f"obs_{uuid.uuid4().hex[:8]}",
                screenshot_path=screenshot_path,
                screen_purpose="Failed to parse AI response",
                screen_type=ScreenType.UNKNOWN,
                confidence=0.0,
                raw_ai_response=raw_text,
            )

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

        nav_data = data.get("navigation_context", {})
        nav_context = NavigationContext(
            visible_navigation=nav_data.get("visible_navigation", []),
            active_navigation=nav_data.get("active_navigation"),
            back_available=nav_data.get("back_available", True),
            inferred_depth=nav_data.get("inferred_depth", 0),
        )

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

    def _parse_context_response(
        self,
        raw_text: str,
        app_store_text: str | None,
        web_search_results: str | None,
    ) -> PriorContext:
        text = self._strip_json_fences(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse context response: %s", e)
            return PriorContext(app_name="Unknown")

        capabilities = []
        for cap_data in data.get("expected_capabilities", []):
            capabilities.append(Capability(
                name=cap_data.get("name", ""),
                description=cap_data.get("description", ""),
                estimated_importance=cap_data.get("estimated_importance", 0.5),
                estimated_frequency=cap_data.get("estimated_frequency", 0.5),
                is_differentiator=cap_data.get("is_differentiator", False),
                provenance=Provenance.AUTOMATED_DISCOVERY,
            ))

        entities = []
        for ent_data in data.get("expected_entities", []):
            entities.append(Entity(
                name=ent_data.get("name", ""),
                description=ent_data.get("description", ""),
                provenance=Provenance.AUTOMATED_DISCOVERY,
            ))

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

    def _parse_world_model_response(
        self,
        raw_text: str,
        current_model: ApplicationModel,
        observations: list[ScreenObservation],
    ) -> ApplicationModel:
        text = self._strip_json_fences(raw_text)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse world model response: %s", e)
            return current_model

        screens = []
        for s_data in data.get("screens", []):
            screen_type_str = s_data.get("screen_type", "unknown")
            try:
                screen_type = ScreenType(screen_type_str)
            except ValueError:
                screen_type = ScreenType.UNKNOWN

            last_obs_id = ""
            for obs in observations:
                if obs.screen_type == screen_type or obs.screen_purpose in s_data.get("purpose", ""):
                    last_obs_id = obs.observation_id
                    break

            existing = next(
                (s for s in current_model.screens if s.screen_id == s_data.get("screen_id")),
                None,
            )
            visit_count = (existing.visit_count + 1) if existing else 1

            screens.append(ScreenNode(
                screen_id=s_data.get("screen_id", f"screen_{uuid.uuid4().hex[:6]}"),
                name=s_data.get("name", "Unknown"),
                screen_type=screen_type,
                purpose=s_data.get("purpose", ""),
                participates_in=s_data.get("participates_in", []),
                visit_count=visit_count,
                last_observation_id=last_obs_id or (existing.last_observation_id if existing else ""),
            ))

        transitions = []
        for t_data in data.get("transitions", []):
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
            coverage_estimate=data.get("coverage_estimate", current_model.coverage_estimate),
            confidence=data.get("confidence", current_model.confidence),
        )

    # ------------------------------------------------------------------
    # Summarisation helpers — identical to AnthropicAI
    # ------------------------------------------------------------------

    def _summarise_model(self, model: ApplicationModel) -> str:
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
            parts.append(f"Differentiators: {', '.join(model.differentiators)}")
        if model.entities:
            parts.append(f"\nEntities: {', '.join(e.name for e in model.entities)}")
        if model.capabilities:
            parts.append("\nCapabilities:")
            for cap in model.capabilities:
                parts.append(
                    f"  - {cap.name} (importance={cap.estimated_importance:.1f}, "
                    f"freq={cap.estimated_frequency:.1f}, diff={cap.is_differentiator})"
                )
        if model.screens:
            parts.append("\nKnown screens:")
            for s in model.screens:
                parts.append(
                    f"  - [{s.screen_id}] {s.name} ({s.screen_type}) "
                    f"visits={s.visit_count}, participates_in=[{', '.join(s.participates_in)}]"
                )
        if model.transitions:
            parts.append("\nKnown transitions:")
            for t in model.transitions:
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
