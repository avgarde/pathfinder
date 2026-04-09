"""Layer 0: Context Gathering — acquiring external knowledge about an app
before exploration begins.

This layer takes an AppReference and produces a PriorContext by:
1. Searching for the app on the web / app stores
2. Extracting relevant information from search results and listings
3. Merging with any user-supplied description and hints
4. Using AI to synthesise everything into structured context

The layer can operate with varying levels of available information:
- Rich: public app with app store listing, documentation, reviews
- Moderate: known app name/category, user-supplied description
- Minimal: just a package name or APK with no external info
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Awaitable

from pathfinder.ai.interface import AIInterface
from pathfinder.contracts.app_reference import AppReference
from pathfinder.contracts.common import Provenance
from pathfinder.contracts.prior_context import (
    Capability,
    ContextSource,
    Entity,
    PriorContext,
)

logger = logging.getLogger(__name__)


# Type for pluggable web search/fetch functions
WebSearchFn = Callable[[str], Awaitable[str]]
WebFetchFn = Callable[[str], Awaitable[str]]


class ContextGatheringLayer:
    """Layer 0: Gather external context about an application.

    Standalone invocation:
        layer = ContextGatheringLayer(ai=my_ai)
        context = await layer.gather(AppReference(package_name="com.spotify.music"))

    With web search:
        layer = ContextGatheringLayer(ai=my_ai, web_search=my_search_fn, web_fetch=my_fetch_fn)
        context = await layer.gather(AppReference(name="Spotify"))
    """

    def __init__(
        self,
        ai: AIInterface,
        web_search: WebSearchFn | None = None,
        web_fetch: WebFetchFn | None = None,
    ):
        self.ai = ai
        self.web_search = web_search
        self.web_fetch = web_fetch

    async def gather(self, app_ref: AppReference) -> PriorContext:
        """Gather context from all available sources and synthesise.

        Args:
            app_ref: The application reference (name, package, URL, etc.)

        Returns:
            A PriorContext with synthesised knowledge about the app.
        """
        logger.info("Gathering context for: %s", app_ref.name or app_ref.package_name or "unknown")

        # Phase 1: Gather raw information from available sources
        app_store_text = None
        web_search_text = None

        if self.web_search:
            # Search for app store listing
            search_query = self._build_search_query(app_ref)
            if search_query:
                logger.info("Searching web for: %s", search_query)
                try:
                    web_search_text = await self.web_search(search_query)
                    logger.info("Web search returned %d chars", len(web_search_text or ""))
                except Exception as e:
                    logger.warning("Web search failed: %s", e)

        if self.web_fetch and app_ref.app_store_url:
            logger.info("Fetching app store listing: %s", app_ref.app_store_url)
            try:
                app_store_text = await self.web_fetch(app_ref.app_store_url)
                logger.info("App store fetch returned %d chars", len(app_store_text or ""))
            except Exception as e:
                logger.warning("App store fetch failed: %s", e)

        # Phase 2: Synthesise with AI
        context = await self.ai.synthesise_context(
            app_name=app_ref.name,
            package_name=app_ref.package_name,
            raw_description=app_ref.description,
            app_store_text=app_store_text,
            web_search_results=web_search_text,
            user_description=app_ref.description,
            baseline=app_ref.baseline,
            differentiation=app_ref.differentiation,
        )

        # Phase 3: Enrich with user-supplied provenance
        if app_ref.description:
            # Mark user-supplied info with correct provenance
            for cap in context.expected_capabilities:
                if not cap.provenance:
                    cap.provenance = Provenance.AUTOMATED_DISCOVERY
            context.sources.append(
                ContextSource(
                    source_type="user_supplied",
                    summary="User-provided app description",
                )
            )

        logger.info(
            "Context gathered: %s (%s) — %d capabilities, %d entities",
            context.app_name,
            context.category or "unknown category",
            len(context.expected_capabilities),
            len(context.expected_entities),
        )

        return context

    async def gather_from_description_only(
        self, description: str, app_name: str | None = None
    ) -> PriorContext:
        """Gather context from just a text description (no web search).

        Useful for internal/pre-release apps where no public info exists.
        """
        return await self.ai.synthesise_context(
            app_name=app_name,
            user_description=description,
        )

    def _build_search_query(self, app_ref: AppReference) -> str | None:
        """Build a web search query from the app reference."""
        parts = []

        if app_ref.name:
            parts.append(app_ref.name)
        elif app_ref.package_name:
            parts.append(app_ref.package_name)
        else:
            return None

        parts.append("app")

        # Prefer Play Store for Android
        parts.append("Google Play Store")

        return " ".join(parts)

    @staticmethod
    def context_to_json(context: PriorContext, indent: int = 2) -> str:
        """Serialise a PriorContext to JSON string."""
        return context.model_dump_json(indent=indent)

    @staticmethod
    def context_from_json(json_str: str) -> PriorContext:
        """Deserialise a PriorContext from JSON string."""
        return PriorContext.model_validate_json(json_str)

    @staticmethod
    def save_context(context: PriorContext, path: str) -> None:
        """Save a PriorContext to a JSON file."""
        Path(path).write_text(context.model_dump_json(indent=2))
        logger.info("Context saved to %s", path)

    @staticmethod
    def load_context(path: str) -> PriorContext:
        """Load a PriorContext from a JSON file."""
        return PriorContext.model_validate_json(Path(path).read_text())
