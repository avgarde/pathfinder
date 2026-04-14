"""Prompt templates for exploration planning (Orchestrator AI capability)."""

from __future__ import annotations


EXPLORATION_PLANNER_SYSTEM_PROMPT = """\
You are an exploration planner for Pathfinder, a system that discovers \
meaningful user flows in applications.

Given the current application model (what we know so far) and the current \
screen observation (what we see right now), decide what action to take next \
to continue exploring the application productively.

Key principles:
- PRIORITISE GOALS: if Exploration Goals are listed, actively pursue them.
  Move toward goal-relevant screens rather than following the default frontier.
  Report a goal as confirmed the moment you can see it has been reached.
- PRIORITISE the exploration frontier: if the model has hypotheses about \
screens or capabilities that haven't been confirmed, try to reach them.
- INVESTIGATE anomalies: if something novel was observed, explore it deeper.
- AVOID repeating: don't revisit screens we've already explored thoroughly \
unless they're a necessary waypoint to reach unexplored areas.
- BE PURPOSEFUL: every action should be aimed at discovering new screens, \
confirming or refuting a hypothesis, or reaching an unexplored area. Random \
clicking is not exploration.
- KNOW WHEN TO STOP: if all Exploration Goals are confirmed, or if the \
model has high coverage and the frontier is empty/low-priority, \
recommend stopping.

GOAL CONFIRMATION RULES:
- A goal is "confirmed" when the current screen unambiguously demonstrates \
the described user capability exists in the app. You don't have to fully \
complete the flow — reaching the relevant screen or seeing the key UI element \
is sufficient to confirm the goal.
- List confirmed goals in the `goals_confirmed` field of your response.
- Once all listed goals are confirmed, you may set should_stop=true with \
stop_reason="All exploration goals confirmed".

INPUT HANDLING — critical:
- When you encounter a screen that requires input you don't have (login \
forms, registration, payment details, personal data), REPORT IT as an \
input_required entry and NAVIGATE AWAY. Do NOT try to fill in forms with \
made-up data. Do NOT repeatedly interact with input fields you can't fill.
- A login/registration screen is a BOUNDARY, not a puzzle. Record what \
inputs it needs, then go back and explore other parts of the app.
- EXCEPTION: if the Available Inputs section lists a matching field, use \
the provided value. If the strategy is "generate", the value field contains \
a hint — synthesise something plausible based on the hint and app context.
- For search boxes and filters: if you have an available input for them, \
use it. Otherwise report them as input_required with category "search_query" \
and move on — do NOT type random queries.

Respond with ONLY valid JSON matching the schema below."""


EXPLORATION_PLAN_SCHEMA = """\
{
  "reasoning": "string — brief explanation of why this action was chosen",
  "should_stop": false,
  "stop_reason": "string or null — why exploration should stop (if should_stop is true)",
  "action": {
    "action_type": "one of: tap, type, swipe, back, scroll, wait, navigate",
    "target_text": "string or null — visible text of the element to interact with",
    "target_role": "string or null — semantic role of the element",
    "target_description": "string — human-readable description of what we're interacting with",
    "input_text": "string or null — text to type (for type actions)",
    "url": "string or null — URL to navigate to (for navigate actions)",
    "direction": "string or null — up/down/left/right (for scroll/swipe)",
    "description": "string — human-readable description of the full action"
  },
  "expected_outcome": "string — what we expect to see after this action",
  "exploration_goal": "string — what frontier item or hypothesis this action is pursuing",
  "goals_confirmed": [
    "string — exact text of each exploration goal that is confirmed by the CURRENT screen"
  ],
  "inputs_required": [
    {
      "field": "string — semantic name, e.g. 'username', 'password', 'search_query'",
      "category": "one of: credentials, personal_data, search_query, content, selection, confirmation, payment, other",
      "element_label": "string — visible label of the input element",
      "element_type": "string — 'text_field', 'dropdown', 'checkbox', etc.",
      "placeholder": "string or empty — placeholder text in the input",
      "required": true,
      "notes": "string — why this input is needed, what it gates"
    }
  ]
}

The inputs_required array should list ALL input fields on the current screen \
that the system cannot fill. It may be empty if the screen has no unfilled \
input requirements. ALWAYS populate this when you see a form, login screen, \
or any screen with input fields — even if you plan to navigate away.

The goals_confirmed array should list any exploration goals (from the \
Exploration Goals section) that are confirmed by the CURRENT screen. \
Leave empty if no goals are confirmed on this step. Use the exact goal text."""


def build_exploration_plan_prompt(
    current_model_summary: str,
    current_observation_summary: str,
    action_history: list[str] | None = None,
    max_actions_remaining: int = 50,
    available_inputs: dict[str, str] | None = None,
    exploration_goals: list[str] | None = None,
    confirmed_goals: list[str] | None = None,
) -> str:
    """Build the prompt for planning the next exploration action.

    Args:
        current_model_summary: Text summary of the ApplicationModel.
        current_observation_summary: Text summary of the current ScreenObservation.
        action_history: Recent actions taken (may include stuck warnings).
        max_actions_remaining: Budget left.
        available_inputs: Dict of field→value (or field→hint for generate
                         strategy) that the system can use. Keys are
                         semantic field names like "username", "search_query".
        exploration_goals: User-specified goals to pursue during exploration.
        confirmed_goals: Goals already confirmed in previous steps (to skip).
    """
    parts = [
        "Decide the next exploration action based on the current state.",
        f"\n--- Application Model (what we know) ---\n{current_model_summary}\n--- End Model ---",
        f"\n--- Current Screen (what we see) ---\n{current_observation_summary}\n--- End Screen ---",
    ]

    # Goal-directed section
    if exploration_goals:
        confirmed_set = set(confirmed_goals or [])
        unconfirmed = [g for g in exploration_goals if g not in confirmed_set]
        parts.append("\n--- Exploration Goals ---")
        for g in exploration_goals:
            status = "✓ CONFIRMED" if g in confirmed_set else "○ PENDING"
            parts.append(f"  [{status}] {g}")
        if unconfirmed:
            parts.append(
                f"\nACTIVELY PURSUE these unconfirmed goals: "
                + ", ".join(f'"{g}"' for g in unconfirmed)
            )
            parts.append(
                "Navigate toward parts of the app that would demonstrate "
                "or confirm these capabilities."
            )
        else:
            parts.append(
                "\nAll goals are confirmed. You may stop or continue "
                "exploring if there is budget remaining and new areas to cover."
            )
        parts.append("--- End Goals ---")

    if action_history:
        recent = action_history[-10:]  # Last 10 actions
        parts.append(
            f"\n--- Recent Action History ---\n"
            + "\n".join(f"  {i+1}. {a}" for i, a in enumerate(recent))
            + "\n--- End History ---"
        )

    if available_inputs:
        parts.append("\n--- Available Inputs ---")
        for field_name, value in available_inputs.items():
            parts.append(f"  {field_name}: {value}")
        parts.append(
            "Use these values when you encounter matching input fields. "
            "If a value starts with 'generate:', synthesise a plausible "
            "value based on the hint after the colon and the app context."
        )
        parts.append("--- End Available Inputs ---")

    parts.append(f"\nExploration budget remaining: {max_actions_remaining} actions")

    if exploration_goals:
        unconfirmed_count = len(
            [g for g in exploration_goals if g not in set(confirmed_goals or [])]
        )
        if unconfirmed_count == 0:
            parts.append(
                "\nAll exploration goals confirmed. "
                "You may stop (set should_stop=true) if the budget is low "
                "or there is nothing new to explore."
            )
        else:
            parts.append(
                f"\n{unconfirmed_count} exploration goal(s) still pending. "
                "Prioritise actions that move toward confirming them."
            )

    parts.append(
        "\nChoose the SINGLE most productive next action. "
        "If there's nothing left to explore or the budget is very low, "
        "set should_stop to true."
    )

    parts.append(
        "\nIMPORTANT: If this screen has input fields you cannot fill "
        "(and no matching Available Input is provided), list them in "
        "inputs_required and navigate away — do NOT attempt to fill them "
        "with made-up data."
    )

    parts.append(f"\nRespond with ONLY valid JSON in this schema:\n{EXPLORATION_PLAN_SCHEMA}")

    return "\n".join(parts)
