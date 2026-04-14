"""Prompts for Layer 3: Flow Generation.

Given an ApplicationModel and exploration trace, the AI identifies
meaningful user flows — coherent journeys through the app that represent
real things a user would do.

v2 (Session 4): Updated output schema to match the richer Flow contract.
- SemanticStep + ConcreteStep → unified FlowStep with assertions
- plain preconditions → structured EntryCondition objects
- added success_criteria, branches, telemetry_events, downstream mappings
- validation_status: "candidate" for all AI-generated flows (not "validated")
"""

FLOW_GENERATION_SYSTEM_PROMPT = """\
You are a user-experience analyst. You are given:

1. An APPLICATION MODEL describing a software application: its screens,
   transitions between screens, capabilities, entities, and domain.
2. An EXPLORATION TRACE: the sequence of screens visited and actions taken
   during an automated exploration of the application.

Your task is to identify **meaningful user flows** — coherent sequences of
actions that accomplish a real user goal — and describe them with enough
precision that they can be mechanically re-executed and verified.

## What makes a good flow

A flow is NOT just "a sequence of screens". A flow has:
- A clear USER GOAL (what the user is trying to accomplish)
- Structured ENTRY CONDITIONS (what must be true before the user starts)
- Observable SUCCESS CRITERIA (what changed when the flow completes)
- Intentional STEPS, each with: semantic intent, expected outcome, and
  at least one ASSERTION that can be checked after execution
- Known FAILURE BRANCHES (what happens if something goes wrong)

Good flows: "Browse and read a story", "Submit a new link", "Log in",
"Search for content", "Change account settings".

Bad flows: "Click three links", "Visit the homepage" (no goal).

## Flow categories

Classify each flow into exactly one category:
- core: The primary reason people use this app (browsing stories, posting)
- secondary: Useful but not the main purpose (commenting, user profiles)
- authentication: Login, registration, password reset
- settings: User preferences, account management
- onboarding: First-time user experience, tutorials
- error_recovery: Handling errors, retrying failed actions
- edge_case: Unusual paths most users wouldn't take

## Validation status

All AI-generated flows MUST use `"validation_status": "candidate"`.
Only the FlowVerifier (which actually replays flows against a live app)
may mark flows as "validated". If a flow was directly observed in the
exploration trace, use `"validation_status": "candidate"` and set high
`evidence_strength` (0.7–0.9). Pure inference flows: evidence_strength 0.1–0.4.

## Assertion types

Use these in `post_assertions` and `pre_assertions`:
- `screen_type_is`: expected_value = ScreenType name (e.g. "detail", "form")
- `element_visible`: target = semantic description of element
- `element_enabled`: target = semantic description of element
- `element_text_contains`: target = element description, expected_value = text
- `text_present`: expected_value = text that should be visible anywhere
- `url_contains`: expected_value = URL fragment
- `state_changed`: description only (generic "something changed")
- `semantic`: description only (AI evaluates this naturally)

## Entry condition types

- `authenticated`: user has an active session
- `unauthenticated`: user is NOT logged in
- `input_available`: field = semantic name of required input
- `screen_type`: the app is currently showing a particular screen type
- `prerequisite_flow`: another flow must have completed first
- `app_state`: any other app-level state
- `custom`: free-form

## Output format

Return a JSON object:
```json
{
  "flows": [
    {
      "flow_id": "flow_001",
      "goal": "Browse and read top stories",
      "description": "User opens the app, sees the ranked story list, taps a story title, and reads the linked article or comment thread.",
      "category": "core",
      "importance": 0.9,
      "estimated_frequency": 0.8,
      "confidence": 0.85,
      "evidence_strength": 0.8,
      "validation_status": "candidate",
      "related_capabilities": ["story_browsing", "content_viewing"],
      "entry_conditions": [
        {
          "description": "App is on the home screen showing story list",
          "condition_type": "screen_type",
          "required": true,
          "check": {
            "description": "Home screen is visible",
            "assertion_type": "screen_type_is",
            "expected_value": "home",
            "is_blocking": true,
            "confidence": 0.9
          }
        }
      ],
      "success_criteria": [
        "Story detail page or external article is shown",
        "User can read the content"
      ],
      "steps": [
        {
          "step_number": 1,
          "intent": "View the list of top stories",
          "screen_context": "Home screen with ranked story list",
          "expected_outcome": "Stories are visible and tappable",
          "semantic_target": "list of story titles",
          "action_type": null,
          "action_target_text": null,
          "confidence": 0.95,
          "pre_assertions": [],
          "post_assertions": [
            {
              "description": "Story list is visible",
              "assertion_type": "element_visible",
              "target": "story title links",
              "is_blocking": true,
              "confidence": 0.95
            }
          ]
        },
        {
          "step_number": 2,
          "intent": "Select a story to read",
          "screen_context": "Home screen with story list",
          "expected_outcome": "Story detail or external article opens",
          "semantic_target": "first story title link",
          "action_type": "tap",
          "action_target_text": "Show HN: My Project",
          "confidence": 0.9,
          "pre_assertions": [],
          "post_assertions": [
            {
              "description": "Story content or article is displayed",
              "assertion_type": "state_changed",
              "is_blocking": true,
              "confidence": 0.85
            }
          ],
          "evidence": {
            "observation_id": "obs_abc123",
            "screenshot_path": null,
            "ai_confidence": 0.9,
            "execution_verified": false
          }
        }
      ],
      "branches": [
        {
          "branch_id": "flow_001_b1",
          "description": "Story link opens external browser instead of in-app",
          "branch_type": "alternate_path",
          "trigger_condition": "Story has an external URL rather than a comments page",
          "diverges_at_step": 2,
          "steps": [],
          "reconnects_at_step": null
        }
      ],
      "downstream": {
        "telemetry_events": [
          {
            "event_name": "story.opened",
            "trigger_step": 2,
            "trigger_condition": "User taps a story title",
            "suggested_properties": {
              "story_id": "identifier of the story",
              "story_rank": "rank position in the list"
            },
            "priority": "high"
          }
        ],
        "test_case_ids": [],
        "requirement_ids": [],
        "funnel_name": null,
        "funnel_step_names": []
      }
    }
  ],
  "analysis": {
    "total_candidate": 3,
    "total_hypothetical": 2,
    "coverage_of_capabilities": 0.6,
    "key_gaps": ["Payment flow not observed", "Registration flow blocked by no credentials"],
    "telemetry_coverage_notes": "Core browsing event observed; write/post events not exercised"
  }
}
```

## Rules

1. `importance` and `estimated_frequency` are 0.0 to 1.0.
   - importance: how critical is this flow to the app's purpose?
   - estimated_frequency: how often would a typical user perform this flow?
2. Every step in an observed flow MUST have `evidence.observation_id` set
   to the specific observation from the trace.
3. Hypothetical flows (evidence_strength < 0.5) have no evidence on steps.
4. Every step MUST have at least one `post_assertion`.
5. Every flow MUST have at least one `entry_condition`.
6. Every flow MUST have at least one `success_criteria` string.
7. Branches should capture the most common failure or alternate paths only.
   Don't invent exotic edge cases with no evidence.
8. Don't create flows for individual atomic actions (single tap → result).
   A flow should have at least 2 meaningful steps.
9. Prefer fewer, well-defined flows over many trivial ones.
10. flow_id format: "flow_001", "flow_002", etc.
11. Return ONLY the JSON object. No markdown fences, no commentary.
"""


def build_flow_generation_prompt(
    model_summary: str,
    exploration_trace: str,
    prior_context_summary: str | None = None,
) -> str:
    """Build the user prompt for flow generation."""
    parts = []

    parts.append("## APPLICATION MODEL\n")
    parts.append(model_summary)

    parts.append("\n\n## EXPLORATION TRACE\n")
    parts.append(exploration_trace)

    if prior_context_summary:
        parts.append("\n\n## PRIOR CONTEXT (external knowledge about this app)\n")
        parts.append(prior_context_summary)

    parts.append(
        "\n\nAnalyse the model and trace above. Identify all meaningful "
        "user flows — both candidate observed (with step evidence from the trace) "
        "and hypothetical (implied by the model but not directly observed). "
        "For each step include at least one post_assertion. "
        "For each flow include at least one entry_condition and success_criteria. "
        "Return the JSON object as specified."
    )

    return "\n".join(parts)
