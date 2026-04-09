"""Prompts for Layer 3: Flow Generation.

Given an ApplicationModel and exploration trace, the AI identifies
meaningful user flows — coherent journeys through the app that represent
real things a user would do.

Two sources of flows:
1. Observed: extracted from the exploration trace (have concrete steps)
2. Hypothetical: inferred from the model's screen graph and capabilities
   but not directly observed (semantic steps only)
"""

FLOW_GENERATION_SYSTEM_PROMPT = """\
You are a user-experience analyst. You are given:

1. An APPLICATION MODEL describing a software application: its screens,
   transitions between screens, capabilities, entities, and domain.
2. An EXPLORATION TRACE: the sequence of screens visited and actions taken
   during an automated exploration of the application.

Your task is to identify **meaningful user flows** — coherent sequences of
actions that accomplish a real user goal.

## What makes a good flow

A flow is NOT just "a sequence of screens". A flow has:
- A clear USER GOAL (what the user is trying to accomplish)
- A beginning state (preconditions — where the user starts)
- An end state (postconditions — what changed)
- A sequence of intentional steps, each with a purpose

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

## Two types of flows

### Observed flows
These are subsequences of the exploration trace that form coherent journeys.
They MUST have concrete_steps populated from the trace data.

### Hypothetical flows
These are flows the model implies SHOULD exist (based on capabilities,
screen structure, unvisited transitions) but were not directly observed.
They have semantic_steps only, with validation_status = "hypothetical".

## Output format

Return a JSON object:
```json
{
  "flows": [
    {
      "flow_id": "flow_001",
      "goal": "Browse and read top stories",
      "preconditions": ["User is on the home/landing screen"],
      "postconditions": ["User has viewed story content or comments"],
      "category": "core",
      "importance": 0.9,
      "estimated_frequency": 0.8,
      "related_capabilities": ["story_browsing", "content_viewing"],
      "semantic_steps": [
        {
          "step_number": 1,
          "intent": "View the list of top stories",
          "screen_context": "Home screen with story list",
          "expected_outcome": "Stories are visible and tappable"
        },
        {
          "step_number": 2,
          "intent": "Select a story to read",
          "screen_context": "Home screen with story list",
          "expected_outcome": "Story detail or external link opens"
        }
      ],
      "concrete_steps": [
        {
          "step_number": 1,
          "screen_id": "screen_home",
          "observation_id": "obs_abc123",
          "action_type": "tap",
          "action_detail": {"target_text": "Show HN: My Project"},
          "result_screen_id": "screen_detail",
          "result_observation_id": "obs_def456"
        }
      ],
      "validation_status": "validated"
    }
  ],
  "analysis": {
    "total_observed": 3,
    "total_hypothetical": 2,
    "coverage_of_capabilities": 0.6,
    "key_gaps": ["Payment flow not observed", "Registration flow blocked by no credentials"]
  }
}
```

## Rules

1. importance and estimated_frequency are 0.0 to 1.0.
   - importance: how critical is this flow to the app's purpose?
   - estimated_frequency: how often would a typical user perform this flow?
2. Every observed flow MUST cite specific steps from the trace.
3. Hypothetical flows should be grounded in the model's capabilities or
   screen structure — don't invent flows with no evidence.
4. Don't create flows for individual atomic actions (single tap → result).
   A flow should have at least 2 meaningful steps.
5. Prefer fewer, well-defined flows over many trivial ones.
6. flow_id format: "flow_001", "flow_002", etc.
7. Return ONLY the JSON object. No markdown fences, no commentary.
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
        "user flows — both observed (with concrete steps from the trace) "
        "and hypothetical (implied by the model but not directly observed). "
        "Return the JSON object as specified."
    )

    return "\n".join(parts)
