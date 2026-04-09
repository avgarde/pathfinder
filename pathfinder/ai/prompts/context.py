"""Prompt templates for the context gathering (Layer 0) AI capability."""

from __future__ import annotations


CONTEXT_SYNTHESIS_SYSTEM_PROMPT = """\
You are a context analysis engine for Pathfinder, a system that discovers \
meaningful user flows in mobile and web applications.

Your job is to take raw information about an application — app store \
descriptions, web search results, user-supplied descriptions — and synthesise \
it into a structured understanding of what the app does, what entities it \
deals with, and what capabilities it offers to users.

Key principles:
- Focus on CAPABILITIES: what can a user DO with this app? Each capability \
should be a concrete action or workflow (e.g., "Search for and play a song", \
not "Music features").
- Identify ENTITIES: what concepts/objects does the app deal with? (e.g., \
songs, playlists, artists, albums for a music app).
- Estimate IMPORTANCE: which capabilities are core (the main reason the app \
exists) vs. secondary (nice-to-have features)?
- Estimate FREQUENCY: which capabilities would a typical user use most often?
- When provided with baseline/differentiation hints, use them to distinguish \
standard category features from what makes this app unique.

Respond with ONLY valid JSON matching the schema below. No markdown, no \
explanation, just the JSON object."""


CONTEXT_SYNTHESIS_SCHEMA = """\
{
  "app_name": "string — canonical app name",
  "category": "string — app category (e.g., 'e-commerce', 'social media', 'video streaming')",
  "description": "string — one-paragraph synthesised description of what the app does",
  "expected_capabilities": [
    {
      "name": "string — short name for the capability",
      "description": "string — what the user can accomplish",
      "estimated_importance": 0.8,
      "estimated_frequency": 0.7,
      "is_differentiator": false
    }
  ],
  "expected_entities": [
    {
      "name": "string — entity name (e.g., 'Product', 'Playlist')",
      "description": "string — what this entity represents in the app"
    }
  ]
}"""


def build_context_synthesis_prompt(
    app_name: str | None = None,
    package_name: str | None = None,
    raw_description: str | None = None,
    app_store_text: str | None = None,
    web_search_results: str | None = None,
    user_description: str | None = None,
    baseline: str | None = None,
    differentiation: str | None = None,
) -> str:
    """Build the prompt for synthesising app context from gathered sources."""
    parts = ["Analyse the following information about a mobile/web application and produce a structured context model."]

    if app_name:
        parts.append(f"\nApp name: {app_name}")
    if package_name:
        parts.append(f"\nPackage/bundle ID: {package_name}")

    if app_store_text:
        # Truncate very long descriptions
        text = app_store_text
        if len(text) > 8000:
            text = text[:8000] + "\n... (truncated)"
        parts.append(f"\n--- App Store Listing ---\n{text}\n--- End App Store Listing ---")

    if web_search_results:
        text = web_search_results
        if len(text) > 5000:
            text = text[:5000] + "\n... (truncated)"
        parts.append(f"\n--- Web Search Results ---\n{text}\n--- End Web Search Results ---")

    if user_description:
        parts.append(f"\n--- User-Supplied Description ---\n{user_description}\n--- End User-Supplied Description ---")

    if raw_description:
        parts.append(f"\n--- Raw Description ---\n{raw_description}\n--- End Raw Description ---")

    if baseline:
        parts.append(
            f"\nBASELINE (standard features for this app category): {baseline}"
        )

    if differentiation:
        parts.append(
            f"\nDIFFERENTIATION (what makes this app unique/novel): {differentiation}\n"
            f"Pay special attention to these differentiating features — they are "
            f"likely the most important capabilities to discover during exploration. "
            f"Mark them with is_differentiator: true."
        )

    parts.append(f"\nRespond with ONLY valid JSON in this schema:\n{CONTEXT_SYNTHESIS_SCHEMA}")

    return "\n".join(parts)
