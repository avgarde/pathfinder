"""Prompt templates for the world modeling (Layer 2) AI capability."""

from __future__ import annotations


WORLD_MODEL_SYSTEM_PROMPT = """\
You are a world-modeling engine for Pathfinder, a system that discovers \
meaningful user flows in mobile and web applications.

Your job is to take the current application model (which may be empty or \
partially built) along with new screen observations, and produce an updated \
model of what the application is, what it does, and what remains to be explored.

Key principles:
- ACCUMULATE knowledge: each update should add to the model, not replace it. \
Previous observations remain valid unless contradicted.
- IDENTIFY screens: determine whether a new observation is a new screen or a \
revisit of a known screen. Two observations of the same screen type with similar \
purpose and overlapping elements are likely the same screen, even if specific \
data content differs (e.g., two product detail pages for different products \
are the same screen type).
- EXTRACT capabilities: from the screens and elements observed, infer what the \
app lets users accomplish. A form with "Ship to" fields suggests a checkout \
capability. A search bar suggests search capability.
- DETECT anomalies: compare observations against expectations. If the prior \
context predicted a capability that hasn't been found, note it as absent. If \
you observe something unexpected, note it as novel.
- MAINTAIN the frontier: what should be explored next? What screens or \
capabilities probably exist but haven't been confirmed?

Respond with ONLY valid JSON matching the schema below. No markdown, no \
explanation, just the JSON object."""


WORLD_MODEL_UPDATE_SCHEMA = """\
{
  "domain": "string — app domain (e.g., 'music streaming', 'e-commerce')",
  "purpose": "string — one-sentence description of the app's purpose",
  "baseline_category": "string or null — what known category this most resembles",
  "differentiators": ["list of strings — what's novel/different about this app"],
  "entities": [
    {
      "name": "string — entity name",
      "description": "string — what this entity represents"
    }
  ],
  "capabilities": [
    {
      "name": "string — capability name",
      "description": "string — what the user can accomplish",
      "estimated_importance": 0.8,
      "estimated_frequency": 0.7,
      "is_differentiator": false,
      "status": "one of: confirmed, hypothesised, absent"
    }
  ],
  "screens": [
    {
      "screen_id": "string — stable identifier for this screen",
      "name": "string — human-readable name",
      "screen_type": "string — from ScreenType enum",
      "purpose": "string — what this screen is for",
      "participates_in": ["list of capability names this screen is part of"],
      "is_new": true
    }
  ],
  "transitions": [
    {
      "from_screen": "string — screen_id",
      "to_screen": "string — screen_id",
      "action": "string — what action triggers this transition"
    }
  ],
  "anomalies": [
    {
      "description": "string — what's unexpected",
      "classification": "one of: novel, absent, contradictory",
      "exploration_priority_boost": 0.3
    }
  ],
  "frontier": [
    {
      "description": "string — what to explore next",
      "rationale": "string — why we think this exists or matters",
      "priority": 0.7,
      "search_strategy": "string — how to look for it"
    }
  ],
  "coverage_estimate": 0.3,
  "confidence": 0.5
}"""


SCREEN_IDENTITY_SCHEMA = """\
{
  "matches": [
    {
      "observation_id": "string — the observation being classified",
      "matched_screen_id": "string or null — existing screen_id if this is a revisit, null if new",
      "confidence": 0.9,
      "reasoning": "string — why this is a match or new screen"
    }
  ]
}"""


def build_world_model_update_prompt(
    current_model_summary: str,
    new_observations_summary: str,
    prior_context_summary: str | None = None,
) -> str:
    """Build the prompt for updating the world model."""
    parts = [
        "Update the application model based on new observations.",
        f"\n--- Current Application Model ---\n{current_model_summary}\n--- End Current Model ---",
        f"\n--- New Observations ---\n{new_observations_summary}\n--- End New Observations ---",
    ]

    if prior_context_summary:
        parts.append(
            f"\n--- Prior Context (from Layer 0) ---\n{prior_context_summary}\n--- End Prior Context ---"
        )

    parts.append(
        "\nProduce an UPDATED model that integrates the new observations. "
        "Include ALL previously known screens, entities, and capabilities "
        "(not just the new ones). Update visit counts and confidence as appropriate."
    )

    parts.append(f"\nRespond with ONLY valid JSON in this schema:\n{WORLD_MODEL_UPDATE_SCHEMA}")

    return "\n".join(parts)


def build_screen_identity_prompt(
    known_screens_summary: str,
    new_observations_summary: str,
) -> str:
    """Build the prompt for identifying whether observations match known screens."""
    parts = [
        "Determine whether each new observation corresponds to a known screen or is a new screen.",
        f"\n--- Known Screens ---\n{known_screens_summary}\n--- End Known Screens ---",
        f"\n--- New Observations ---\n{new_observations_summary}\n--- End New Observations ---",
        "\nFor each observation, decide: is this a revisit of a known screen "
        "(same screen type and purpose, possibly with different data) or a "
        "genuinely new screen? Two product detail pages showing different products "
        "are the SAME screen. A product detail page and a cart page are DIFFERENT screens.",
        f"\nRespond with ONLY valid JSON in this schema:\n{SCREEN_IDENTITY_SCHEMA}",
    ]

    return "\n".join(parts)
