"""Input contracts — recording what inputs an app requires and supplying them.

Two complementary data structures:

1. InputRequest — recorded DURING exploration when the system encounters a
   screen or element that requires input it doesn't have. Saved as part of
   the run output so a subsequent run can supply the needed data.

2. InputSpec — supplied BEFORE exploration to tell the system how to handle
   specific input needs. Supports four resolution strategies:

     "literal"  — use this exact value (e.g., a real username/password)
     "generate" — let the AI synthesise a plausible value from context
     "ask"      — pause and prompt the user (interactive mode only)
     "skip"     — don't attempt this input; treat it as an exploration boundary

   InputSpecs are keyed by a semantic field name (e.g., "username", "search_query")
   that the exploration planner uses to match against encountered input fields.

Lifecycle:
    Run 1 (no inputs supplied):
        → system discovers login screen, records InputRequest for "username" and "password"
        → system skips the login, continues exploring public areas
        → run output includes inputs_required.json

    Run 2 (with inputs supplied):
        pathfinder explore-web https://example.com \\
            --inputs inputs.json

        inputs.json:
        [
            {"field": "username", "strategy": "literal", "value": "test_user"},
            {"field": "password", "strategy": "literal", "value": "test_pass"},
            {"field": "search_query", "strategy": "generate"}
        ]

        → system uses these when it encounters the corresponding fields
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InputCategory(str, Enum):
    """What kind of input is being requested."""

    CREDENTIALS = "credentials"     # username, password, API key, token
    PERSONAL_DATA = "personal_data" # name, email, address, phone
    SEARCH_QUERY = "search_query"   # search box, filter text
    CONTENT = "content"             # compose message, write post, comment
    SELECTION = "selection"         # dropdown, radio, checkbox choice
    CONFIRMATION = "confirmation"   # "are you sure?", terms acceptance
    PAYMENT = "payment"             # credit card, billing info
    OTHER = "other"


class InputStrategy(str, Enum):
    """How the system should resolve an input need."""

    LITERAL = "literal"     # Use the provided value exactly
    GENERATE = "generate"   # AI synthesises a plausible value from context
    ASK = "ask"             # Pause and ask the user (requires --interactive)
    SKIP = "skip"           # Don't fill; treat as exploration boundary


class InputRequest(BaseModel):
    """Recorded when the system encounters an input it can't provide.

    Written to inputs_required.json at the end of each run. The user
    can review these, decide on strategies, and create an InputSpec file
    for the next run.
    """

    field: str                          # Semantic name: "username", "search_query", etc.
    category: InputCategory             # What kind of input
    screen_type: str                    # Screen type where this was encountered
    screen_purpose: str                 # What the screen does
    element_label: str = ""             # Visible label of the input element
    element_type: str = ""              # "text_field", "dropdown", etc.
    placeholder: str = ""               # Placeholder text in the input, if any
    required: bool = True               # Whether the screen is gated on this input
    step_number: int = 0                # When during exploration this was encountered
    suggested_strategy: InputStrategy = InputStrategy.SKIP  # System's best guess
    notes: str = ""                     # Why the system thinks this is needed

    model_config = {"use_enum_values": True}


class InputSpec(BaseModel):
    """User-supplied specification for how to handle a particular input.

    Loaded from a JSON file and matched against InputRequests by field name.
    """

    field: str                          # Must match an InputRequest.field
    strategy: InputStrategy             # How to resolve this input
    value: str | None = None            # For "literal": the exact value to use
    generate_hint: str | None = None    # For "generate": hint to guide AI synthesis
    sensitive: bool = False             # If true, value is redacted in logs/output

    model_config = {"use_enum_values": True}

    def resolve(self, context: str = "") -> str | None:
        """Resolve this spec to a concrete value (for literal and skip).

        For "generate" and "ask" strategies, the caller handles resolution
        since those need external capabilities (AI or user interaction).
        """
        if self.strategy == InputStrategy.LITERAL:
            return self.value
        if self.strategy == InputStrategy.SKIP:
            return None
        # "generate" and "ask" need external handling
        return None


class InputRegistry:
    """Tracks input requirements encountered during a run and resolves
    them against supplied InputSpecs.

    The orchestrator creates one of these per run. As exploration proceeds,
    the planner reports input requirements via record(). When the planner
    wants to fill an input, it calls resolve() to get the value (or None
    if the input should be skipped).

    For the "ask" strategy, the caller must provide a prompt_fn callback
    that requests input from the user. This keeps the registry decoupled
    from any particular I/O mechanism (stdin, GUI, etc.).
    """

    def __init__(
        self,
        specs: list[InputSpec] | None = None,
        interactive: bool = False,
        prompt_fn: Any | None = None,
    ):
        self._specs: dict[str, InputSpec] = {}
        self._requests: list[InputRequest] = []
        self._seen_fields: set[str] = set()
        self._ask_cache: dict[str, str] = {}  # Cache answers so we only ask once
        self.interactive = interactive
        # prompt_fn signature: (field: str, context: str) -> str | None
        # Returns the user's input, or None to skip.
        self.prompt_fn = prompt_fn

        if specs:
            for spec in specs:
                self._specs[spec.field] = spec

    def record(self, request: InputRequest) -> None:
        """Record an encountered input requirement.

        Deduplicates by field name — if we've already recorded a request
        for "username", we don't record it again (but the step_number of
        the first encounter is preserved).
        """
        if request.field not in self._seen_fields:
            self._requests.append(request)
            self._seen_fields.add(request.field)

    def has_spec(self, field: str) -> bool:
        """Check whether we have a spec for a given field name."""
        return field in self._specs

    def get_strategy(self, field: str) -> InputStrategy:
        """Get the resolution strategy for a field. Defaults to SKIP."""
        if field in self._specs:
            return InputStrategy(self._specs[field].strategy)
        return InputStrategy.SKIP

    def resolve(self, field: str, context: str = "") -> str | None:
        """Resolve a field to a concrete value, or None to skip.

        Returns:
            str — the value to use (for literal, generate, or ask)
            None — skip this input (no spec, strategy is skip, or
                    ask failed / not interactive)

        For "generate" strategy, this returns the generate_hint so the
        caller can pass it to the AI.

        For "ask" strategy, this calls the prompt_fn callback (if
        interactive mode is enabled and a prompt_fn was provided).
        Answers are cached so the user is only asked once per field
        per run.
        """
        if field not in self._specs:
            return None

        spec = self._specs[field]

        if spec.strategy == InputStrategy.LITERAL:
            return spec.value

        if spec.strategy == InputStrategy.GENERATE:
            # Return the hint; caller passes this to the AI to synthesise
            return spec.generate_hint or f"generate a plausible value for: {field}"

        if spec.strategy == InputStrategy.ASK:
            if not self.interactive:
                return None  # Can't ask without interactive mode

            # Return cached answer if we already asked
            if field in self._ask_cache:
                return self._ask_cache[field]

            # Prompt the user via the callback
            if self.prompt_fn is not None:
                answer = self.prompt_fn(field, context)
                if answer is not None:
                    self._ask_cache[field] = answer
                    return answer

            return None

        # SKIP
        return None

    @property
    def requests(self) -> list[InputRequest]:
        """All recorded input requests."""
        return list(self._requests)

    @property
    def unresolved_requests(self) -> list[InputRequest]:
        """Input requests that have no spec (or are set to skip)."""
        return [
            r for r in self._requests
            if r.field not in self._specs
            or self._specs[r.field].strategy == InputStrategy.SKIP
        ]

    def to_requests_json(self, indent: int = 2) -> str:
        """Serialise all recorded requests to JSON."""
        import json
        return json.dumps(
            [r.model_dump() for r in self._requests],
            indent=indent,
        )

    @staticmethod
    def load_specs(path: str) -> list[InputSpec]:
        """Load InputSpecs from a JSON file.

        Tolerates trailing commas and // line comments since these files
        are hand-edited. Strips both before passing to the JSON parser.
        """
        import json
        import re
        from pathlib import Path as P

        raw = P(path).read_text()

        # Strip // line comments (but not inside strings — good enough
        # for hand-edited config files)
        raw = re.sub(r'//.*?$', '', raw, flags=re.MULTILINE)

        # Strip trailing commas before } or ]
        raw = re.sub(r',\s*([}\]])', r'\1', raw)

        data = json.loads(raw)
        if isinstance(data, list):
            return [InputSpec.model_validate(item) for item in data]
        raise ValueError(f"Expected a JSON array of input specs, got {type(data).__name__}")
