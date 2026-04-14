"""Prompt templates for the perception (Layer 1) AI capability."""

from __future__ import annotations

from pathfinder.ai.interface import PerceptionContext

PERCEPTION_SYSTEM_PROMPT = """\
You are a screen analysis engine for Pathfinder, a system that discovers \
meaningful user flows in mobile and web applications.

Your job is to look at a screenshot of an application screen (and optionally \
an accessibility tree) and produce a precise, structured analysis of what \
you see. You are the system's eyes — everything downstream depends on the \
quality and accuracy of your observations.

Key principles:
- Be SEMANTIC, not just structural. Don't just say "there's a button". Say \
what the button is FOR, what tapping it would likely DO.
- Identify ALL interactive elements, but distinguish between primary actions \
(the main things a user would do on this screen) and secondary ones (navigation, \
settings, help).
- Infer the screen's PURPOSE in the context of the app. "This is a product \
detail page" is more useful than "this screen has images and text".
- Note the NAVIGATION context: tabs, back buttons, menus, breadcrumbs — \
anything that tells you where this screen sits in the app's structure.
- Infer APP STATE where visible: is the user logged in? Is there data loaded? \
Are there items in a cart? Is this a first-time experience?
- Track SELECTED STATE: for tabs, navigation items, filters, toggles, and \
any element that can be in an active/selected/highlighted state, set \
is_selected to true. This is critical — downstream layers need to know \
where the user currently IS in the app, not just what navigation exists.

Respond with ONLY valid JSON matching the schema below. No markdown, no \
explanation, just the JSON object."""


PERCEPTION_RESPONSE_SCHEMA = """\
{
  "screen_purpose": "string — one sentence describing what this screen is for",
  "screen_type": "one of: login, registration, home, list, detail, form, settings, search, navigation, confirmation, error, loading, onboarding, modal, unknown",
  "app_state": {
    "key": "value pairs of inferred app state, e.g. logged_in: yes, cart_items: 3"
  },
  "elements": [
    {
      "element_id": "string — unique id like 'elem_01'",
      "element_type": "string — button, text_field, link, image, icon, toggle, checkbox, dropdown, tab, list_item, etc.",
      "semantic_role": "string — what this element is FOR, e.g. 'Initiates sign-in process', 'Navigates to product detail'",
      "label": "string — the visible text or accessible label",
      "is_interactive": true,
      "is_enabled": true,
      "is_selected": false,
      "possible_actions": ["tap", "long_press", "type", "swipe"],
      "inferred_destination": "string or null — where interacting might lead",
      "bounds_description": "string — approximate location: 'top-left', 'center', 'bottom navigation bar', etc.",
      "confidence": 0.95
    }
  ],
  "navigation_context": {
    "visible_navigation": ["list of navigation elements: tabs, menu items, etc."],
    "active_navigation": "string or null — which nav element is currently selected/active",
    "back_available": true,
    "inferred_depth": 0
  },
  "confidence": 0.9
}"""


def build_perception_prompt(
    ui_structure_xml: str | None = None,
    context: PerceptionContext | None = None,
) -> str:
    """Build the user-turn prompt for the perception call."""
    parts = ["Analyse the attached screenshot of a mobile application screen."]

    if ui_structure_xml:
        # Truncate very large XML to avoid token waste
        xml = ui_structure_xml
        if len(xml) > 15000:
            xml = xml[:15000] + "\n... (truncated)"
        parts.append(
            f"\nThe following accessibility tree / UI hierarchy is also available:\n"
            f"```xml\n{xml}\n```\n"
            f"Use it to enrich your analysis with precise resource IDs, "
            f"class names, and bounds. But rely on the screenshot for semantic "
            f"understanding — the accessibility tree may be incomplete or misleading."
        )

    if context:
        if context.known_domain:
            parts.append(f"\nKnown app domain: {context.known_domain}")
        if context.known_entities:
            parts.append(
                f"\nKnown entities in this app: {', '.join(context.known_entities)}"
            )
        if context.prior_screen:
            prior = context.prior_screen
            parts.append(
                f"\nPrevious screen (what you just came from): "
                f"{prior.screen_purpose} (type: {prior.screen_type.value}). "
                f"This is important for understanding navigation context — you likely "
                f"navigated FROM that screen to this one via some action."
            )
        if context.navigation_history:
            parts.append(
                f"\nRecent navigation path: {' → '.join(context.navigation_history)}"
            )
        if context.exploration_focus:
            parts.append(
                f"\nExploration focus: {context.exploration_focus}. "
                f"Pay extra attention to elements related to this."
            )

    parts.append(f"\nRespond with ONLY valid JSON in this schema:\n{PERCEPTION_RESPONSE_SCHEMA}")

    return "\n".join(parts)
