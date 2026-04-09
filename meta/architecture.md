# Pathfinder — Architecture & Design Document

**Version:** 0.1
**Date:** 2026-04-05
**Status:** Draft

---

## 1. Purpose & Philosophy

Pathfinder is a system that explores applications (mobile, web) and discovers meaningful user flows — purposeful paths through the application that accomplish something a human user would want to do.

### Core principles

**Intent over mechanism.** The system reasons about what an app is *for* and what a user would want to *accomplish*, rather than enumerating clickable elements. This is the fundamental differentiator from brute-force exploration tools.

**AI as the reasoning engine, automation as a thin servant.** The intelligence lives in multimodal AI models that perceive screens, build world models, and generate hypotheses. The automation layer (ADB, UIAutomator, etc.) is a minimal, replaceable adapter that executes physical interactions.

**Layered independence with shared contracts.** The system is decomposed into layers with well-defined data contracts at each boundary. Each layer can be invoked standalone with standardised input, or composed into an iterative exploration loop.

**Graceful degradation with novelty.** The system works best with known app categories but degrades gracefully to first-principles reasoning for novel apps. Category priors accelerate exploration; their absence slows it but doesn't break it.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Orchestrator                         │
│         (Pipeline mode / Agent loop mode)                │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │
│  │ Layer 0   │→│ Layer 1   │→│ Layer 2   │→│ Layer 3  │ │
│  │ Context   │  │ Perception│  │ World     │  │ Flow    │ │
│  │ Gathering │  │           │  │ Modeling  │  │ Gen     │ │
│  └──────────┘  └──────────┘  └──────────┘  └─────────┘ │
│       │              │              │             │       │
│       └──────────────┴──────┬───────┴─────────────┘      │
│                             │                             │
│                    ┌────────┴────────┐                    │
│                    │  AI Interface   │                    │
│                    │  (Configurable) │                    │
│                    └────────┬────────┘                    │
│                             │                             │
│                    ┌────────┴────────┐                    │
│                    │ Device Adapter  │                    │
│                    │ (Pluggable)     │                    │
│                    └─────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

### Execution modes

**Pipeline mode:** Each layer runs to completion, producing a serialised output that becomes the next layer's input. Useful for batch processing, testing individual layers, and scenarios where partial inputs are hand-authored.

**Agent loop mode:** The orchestrator interleaves calls to all layers in a tight perception→reasoning→action cycle. Each iteration: perceive the current screen (L1), update the world model (L2), decide next action based on exploration strategy (L3/orchestrator), execute via device adapter, repeat.

Both modes produce and consume identical data formats. The difference is purely in composition.

---

## 3. Cross-cutting: AI Interface

All AI model usage goes through a single abstraction. The system never calls a specific model API directly.

### Capabilities

```python
class AIInterface(Protocol):
    """All AI model interactions go through this interface."""

    async def perceive(
        self,
        screenshot: Image,
        context: PerceptionContext | None = None
    ) -> ScreenObservation:
        """Layer 1: Given a screenshot (and optional context like
        accessibility tree, prior screen), return a structured
        semantic observation."""
        ...

    async def update_world_model(
        self,
        current_model: ApplicationModel,
        new_observations: list[ScreenObservation]
    ) -> ApplicationModel:
        """Layer 2: Given the current world model and new observations,
        return an updated world model."""
        ...

    async def generate_flow_hypotheses(
        self,
        model: ApplicationModel
    ) -> list[FlowHypothesis]:
        """Layer 3: Given a world model, generate candidate flows
        worth exploring/validating."""
        ...

    async def judge_flow(
        self,
        flow: Flow,
        model: ApplicationModel
    ) -> FlowAssessment:
        """Layer 3: Assess a validated flow's importance, frequency,
        and category."""
        ...

    async def plan_exploration(
        self,
        model: ApplicationModel,
        goal: ExplorationGoal
    ) -> list[IntendedAction]:
        """Orchestrator: Given the world model and an exploration
        goal, plan the next sequence of actions."""
        ...

    async def gather_context(
        self,
        app_reference: AppReference
    ) -> PriorContext:
        """Layer 0: Given an app reference (name, package, URL),
        gather external context to seed the world model."""
        ...
```

### Configuration

```python
@dataclass
class AIConfig:
    provider: str          # "anthropic", "openai", "local", etc.
    model: str             # "claude-sonnet-4-20250514", "gpt-4o", etc.
    api_key: str | None    # None for local models
    base_url: str | None   # For local model endpoints
    max_tokens: int = 4096
    temperature: float = 0.0
    planning_budget: int = 50  # Max AI calls per exploration cycle
```

A concrete implementation (`AnthropicAI`, `OpenAICompatibleAI`, `LocalModelAI`) implements the `AIInterface` protocol and handles prompt construction, response parsing, and retries internally. The rest of the system is model-agnostic.

---

## 4. Cross-cutting: Device Adapter

All physical interaction with the target application goes through this interface.

```python
class DeviceAdapter(Protocol):
    """Thin interface for interacting with the target device/app."""

    async def get_screenshot(self) -> Image:
        """Capture the current screen."""
        ...

    async def get_ui_structure(self) -> UIStructure | None:
        """Get the accessibility tree / view hierarchy if available.
        Returns None if not supported or extraction fails."""
        ...

    async def perform_action(self, action: DeviceAction) -> ActionResult:
        """Execute a physical interaction."""
        ...

    async def get_app_info(self) -> AppInfo:
        """Get metadata about the currently running app."""
        ...

    async def install_app(self, reference: AppReference) -> None:
        """Install the app from a bundle, URL, or store reference."""
        ...

    async def launch_app(self, package: str) -> None:
        """Launch or restart the app."""
        ...

    async def reset_app_state(self) -> None:
        """Clear app data and return to a clean state."""
        ...

    async def get_device_info(self) -> DeviceInfo:
        """Get device metadata (OS version, screen size, etc.)."""
        ...
```

### Device actions

```python
@dataclass
class TapAction:
    target: ElementReference | Coordinates
    description: str  # Human-readable: "Tap 'Sign In' button"

@dataclass
class TypeAction:
    text: str
    target: ElementReference | Coordinates | None  # None = type into focused field
    description: str

@dataclass
class SwipeAction:
    direction: Literal["up", "down", "left", "right"]
    distance: float  # 0.0 to 1.0, fraction of screen
    description: str

@dataclass
class BackAction:
    description: str = "Navigate back"

@dataclass
class ScrollAction:
    direction: Literal["up", "down"]
    target: ElementReference | None  # Specific scrollable element, or full screen
    description: str

@dataclass
class WaitAction:
    condition: str  # Described in natural language for AI evaluation
    timeout_ms: int = 5000
    description: str

DeviceAction = TapAction | TypeAction | SwipeAction | BackAction | ScrollAction | WaitAction
```

### Element references

```python
@dataclass
class ElementReference:
    """Identifies a UI element. Multiple identification strategies
    are stored; the adapter tries them in order of reliability."""
    resource_id: str | None = None
    text: str | None = None
    content_description: str | None = None
    class_name: str | None = None
    bounds: tuple[int, int, int, int] | None = None  # left, top, right, bottom
    description: str = ""  # Semantic description: "The red 'Add to Cart' button"
```

### Implementations

**Android (MVP):** Wraps ADB + uiautomator2 Python library. Screenshot via `adb exec-out screencap -p`. UI structure via `uiautomator dump`. Actions via uiautomator2's HTTP API.

**iOS (future):** Wraps facebook-wda or idb. Same interface.

**Web (future):** Wraps a browser automation library. Same interface.

---

## 5. Inter-layer Data Contracts

These are the core data structures that flow between layers. They are the architectural spine of the system.

### 5.1 AppReference (System input)

```python
@dataclass
class AppReference:
    """The starting point. How the user identifies the target app."""
    # At least one of these must be provided
    package_name: str | None = None      # e.g., "com.spotify.music"
    app_store_url: str | None = None     # Play Store / App Store URL
    bundle_path: str | None = None       # Local .apk / .ipa path
    web_url: str | None = None           # For web apps
    name: str | None = None              # Human-readable app name

    # Optional context the user can provide
    description: str | None = None       # Free-text description of the app
    baseline: str | None = None          # "What's standard about this app"
    differentiation: str | None = None   # "What's novel about this app"
    credentials: dict[str, str] | None = None  # e.g., {"email": "...", "password": "..."}
```

### 5.2 PriorContext (Layer 0 output)

```python
@dataclass
class PriorContext:
    """External knowledge gathered before exploration begins."""
    app_name: str
    category: str | None                         # e.g., "e-commerce", "social media"
    description: str                             # Synthesised from all sources
    expected_capabilities: list[Capability]       # What the app probably lets you do
    expected_entities: list[Entity]               # What concepts/objects the app deals with
    sources: list[ContextSource]                  # Provenance: where each piece came from

@dataclass
class Capability:
    name: str                           # e.g., "Purchase a product"
    description: str
    estimated_importance: float         # 0.0 to 1.0
    estimated_frequency: float          # 0.0 to 1.0
    provenance: Provenance

@dataclass
class Entity:
    name: str                           # e.g., "Product", "Cart", "Order"
    description: str
    provenance: Provenance

class Provenance(Enum):
    OBSERVED = "observed"                # Seen during exploration
    INFERRED_STRUCTURE = "inferred_structure"  # Inferred from UI patterns
    INFERRED_CATEGORY = "inferred_category"   # Inferred from app category
    EXTERNALLY_SUPPLIED = "externally_supplied" # From app description, docs, user input
    AUTOMATED_DISCOVERY = "automated_discovery" # From web search, app store, etc.
```

### 5.3 ScreenObservation (Layer 1 output)

```python
@dataclass
class ScreenObservation:
    """Structured semantic understanding of a single app screen."""

    # Identity & context
    observation_id: str                  # Unique ID
    timestamp: datetime
    screenshot: Image                    # The raw screenshot
    ui_structure: UIStructure | None     # Accessibility tree if available

    # Semantic understanding (Layer 1's core output)
    screen_purpose: str                  # e.g., "Product detail page for a specific item"
    screen_type: ScreenType              # Categorisation (see below)
    app_state: dict[str, str]            # Inferred state: {"logged_in": "yes", "cart_items": "3"}

    # Elements
    elements: list[UIElement]

    # Navigation
    navigation_context: NavigationContext

    # Confidence
    confidence: float                    # 0.0 to 1.0: how confident is the perception

class ScreenType(Enum):
    LOGIN = "login"
    REGISTRATION = "registration"
    HOME = "home"
    LIST = "list"                        # List of items (products, messages, etc.)
    DETAIL = "detail"                    # Detail view of a single item
    FORM = "form"                        # Data entry form
    SETTINGS = "settings"
    SEARCH = "search"
    NAVIGATION = "navigation"            # Menu, drawer, tab selection
    CONFIRMATION = "confirmation"        # Confirm an action
    ERROR = "error"                      # Error state
    LOADING = "loading"
    ONBOARDING = "onboarding"
    MODAL = "modal"                      # Dialog / bottom sheet
    UNKNOWN = "unknown"

@dataclass
class UIElement:
    """A single UI element with semantic understanding."""
    element_id: str
    reference: ElementReference          # How to interact with it
    element_type: str                    # "button", "text_field", "link", "image", etc.
    semantic_role: str                   # What it's for: "Initiates purchase", "Navigates to cart"
    label: str                           # Visible label/text
    is_interactive: bool
    is_enabled: bool
    possible_actions: list[str]          # ["tap", "long_press", "type"]
    inferred_destination: str | None     # Where tapping might lead: "Cart screen"
    confidence: float

@dataclass
class NavigationContext:
    """Where this screen sits in the app's structure."""
    arrived_from: str | None             # Screen we came from
    arrival_action: str | None           # Action that brought us here
    visible_navigation: list[str]        # Tabs, menu items, etc.
    back_available: bool
    inferred_depth: int                  # How deep in the nav hierarchy (0 = top level)
```

### 5.4 ApplicationModel (Layer 2 output)

```python
@dataclass
class ApplicationModel:
    """The system's evolving understanding of the application."""

    # Identity
    app_reference: AppReference
    model_version: int                   # Incremented on each update

    # Domain understanding
    domain: str                          # e.g., "E-commerce", "Messaging", "Fitness tracking"
    purpose: str                         # One-sentence description of what the app does
    baseline_category: str | None        # What known category this most resembles
    differentiators: list[str]           # What's novel/different about this app

    # Entities
    entities: list[Entity]

    # Capabilities
    capabilities: list[Capability]

    # Screen graph
    screens: list[ScreenNode]
    transitions: list[ScreenTransition]

    # Exploration state
    frontier: list[ExplorationHypothesis]
    anomalies: list[Anomaly]
    coverage_estimate: float             # 0.0 to 1.0: estimated exploration completeness
    confidence: float                    # Overall model confidence

    # Observations log
    observations: list[ScreenObservation]

@dataclass
class ScreenNode:
    """A distinct screen in the app's navigation graph."""
    screen_id: str
    name: str                            # e.g., "Product Detail"
    screen_type: ScreenType
    purpose: str
    participates_in: list[str]           # Capability names this screen is part of
    visit_count: int
    last_observation_id: str             # Most recent ScreenObservation of this screen

@dataclass
class ScreenTransition:
    """An observed transition between screens."""
    from_screen: str                     # screen_id
    to_screen: str                       # screen_id
    action: str                          # Human-readable: "Tap 'Add to Cart'"
    action_detail: DeviceAction          # Concrete action
    observed_count: int

@dataclass
class ExplorationHypothesis:
    """Something the system thinks exists but hasn't confirmed."""
    description: str                     # "There's probably a settings/preferences screen"
    rationale: str                       # Why we think this
    provenance: Provenance
    priority: float                      # 0.0 to 1.0
    search_strategy: str                 # How to look for it

@dataclass
class Anomaly:
    """An observation that doesn't fit the current model."""
    description: str
    observation_id: str                  # The observation that triggered this
    classification: Literal["novel", "absent", "contradictory"]
    exploration_priority_boost: float    # How much to increase exploration here
```

### 5.5 Flow & FlowSet (Layer 3 output)

```python
@dataclass
class Flow:
    """A meaningful user journey through the application."""

    flow_id: str

    # Semantic layer (the "what and why")
    goal: str                            # "Purchase a product"
    preconditions: list[str]             # ["User is logged in", "At least one product exists"]
    postconditions: list[str]            # ["Order is placed", "Confirmation screen shown"]
    semantic_steps: list[SemanticStep]
    category: FlowCategory
    importance: float                    # 0.0 to 1.0
    estimated_frequency: float           # 0.0 to 1.0

    # Concrete trace (the "how, specifically")
    concrete_steps: list[ConcreteStep] | None  # None if hypothetical (not yet validated)
    validation_status: Literal["hypothetical", "validated", "failed"]
    validation_notes: str | None

    # Relationships
    related_capabilities: list[str]      # Capability names from the ApplicationModel
    sub_flows: list[str] | None          # flow_ids if this is composed of smaller flows
    parent_flow: str | None              # flow_id if this is a sub-flow

@dataclass
class SemanticStep:
    """An intent-level step in a flow."""
    step_number: int
    intent: str                          # "Select a product from the catalog"
    screen_context: str                  # "Product listing screen"
    expected_outcome: str                # "Product detail screen is shown"

@dataclass
class ConcreteStep:
    """A recorded, executable step in a flow."""
    step_number: int
    screen_id: str                       # From the ApplicationModel's screen graph
    observation_id: str                  # The ScreenObservation at this point
    action: DeviceAction
    result_screen_id: str
    result_observation_id: str
    duration_ms: int                     # How long this step took

class FlowCategory(Enum):
    CORE = "core"                        # Primary app functionality
    SECONDARY = "secondary"              # Important but not primary
    SETTINGS = "settings"                # Configuration/preferences
    ONBOARDING = "onboarding"            # First-time user experience
    ERROR_RECOVERY = "error_recovery"    # Handling error states
    EDGE_CASE = "edge_case"              # Unusual but valid paths
    AUTHENTICATION = "authentication"    # Login, logout, registration

@dataclass
class FlowSet:
    """The complete output of the system."""
    app_reference: AppReference
    application_model: ApplicationModel
    flows: list[Flow]
    generation_metadata: GenerationMetadata

@dataclass
class GenerationMetadata:
    """How this flow set was produced."""
    timestamp: datetime
    duration_seconds: float
    ai_calls_made: int
    screens_explored: int
    exploration_mode: Literal["pipeline", "agent_loop"]
    ai_config: AIConfig                  # Which model was used
    device_info: DeviceInfo
```

---

## 6. Layer 0: Context Gathering

### Purpose
Produce a `PriorContext` that seeds the world model before any app interaction occurs.

### Input
`AppReference`

### Output
`PriorContext`

### Behaviour

1. **From the AppReference itself:** Extract any user-supplied description, baseline, and differentiation.

2. **Automated discovery** (if app is public):
   - Search for the app on Play Store / App Store
   - Extract: category, description, feature list, screenshots, ratings, reviews
   - Optionally: search the web for the app's marketing site, help docs, blog posts
   - Use AI to synthesise this into expected capabilities and entities

3. **Merge and rank:** Combine all sources into a unified PriorContext. User-supplied information takes precedence (highest confidence). Automated discovery fills gaps.

### Standalone invocation

```
pathfinder context --app "com.spotify.music"
pathfinder context --app-description "A fitness app that tracks weightlifting sets and suggests progressive overload plans"
```

Outputs a `PriorContext` as JSON.

---

## 7. Layer 1: Perception

### Purpose
Given a screenshot (and optionally an accessibility tree and prior context), produce a `ScreenObservation`.

### Input
- `Image` (screenshot) — **required**
- `UIStructure` (accessibility tree) — optional, enriches perception
- `PerceptionContext` — optional (prior screen, navigation history, known app domain)

### Output
`ScreenObservation`

### Behaviour

1. **Capture inputs:** Take screenshot, optionally extract accessibility tree from device adapter.

2. **AI perception call:** Send the screenshot (and UI structure if available) to the AI model with a structured prompt that requests:
   - Screen purpose and type classification
   - Enumeration of interactive elements with semantic roles
   - Inferred app state
   - Navigation context

3. **Merge signals:** If accessibility tree is available, cross-reference AI perception with structural data. Use accessibility tree for precise element coordinates and resource IDs; use AI for semantic understanding.

4. **Produce ScreenObservation:** Structured, serialisable output.

### Perception context

```python
@dataclass
class PerceptionContext:
    """Optional context that improves perception accuracy."""
    prior_screen: ScreenObservation | None = None
    navigation_history: list[str] | None = None  # Recent screen purposes
    known_domain: str | None = None               # From Layer 0 or Layer 2
    known_entities: list[str] | None = None       # Entity names to look for
    exploration_focus: str | None = None           # "Look for settings-related elements"
```

### Standalone invocation

```
pathfinder perceive --screenshot ./screen.png
pathfinder perceive --screenshot ./screen.png --ui-structure ./hierarchy.xml
pathfinder perceive --screenshot ./screen.png --context ./prior_context.json
```

Outputs a `ScreenObservation` as JSON.

---

## 8. Layer 2: World Modeling

### Purpose
Maintain and evolve the `ApplicationModel` by integrating new observations.

### Input
- `ApplicationModel` (current state — may be empty/seed)
- `list[ScreenObservation]` (new observations to integrate)

### Output
Updated `ApplicationModel`

### Behaviour

1. **Screen identification:** Determine if observed screens are new or revisits of known screens (fuzzy matching on purpose, type, and element composition).

2. **Graph update:** Add new screens and transitions to the screen graph.

3. **Entity and capability extraction:** From new observations, infer entities and capabilities not yet in the model.

4. **Anomaly detection:** Compare observations against expectations (from category priors and current model). Classify as confirmed, novel, or absent.

5. **Frontier update:** Based on the updated model, generate or revise hypotheses about unexplored areas. Adjust priorities based on anomalies and coverage.

6. **Confidence update:** Reassess overall model confidence based on coverage and consistency.

### Standalone invocation

```
pathfinder model --observations ./observations/*.json
pathfinder model --current-model ./model.json --observations ./new_observations/*.json
pathfinder model --seed-context ./prior_context.json --observations ./observations/*.json
```

Outputs an `ApplicationModel` as JSON.

---

## 9. Layer 3: Flow Generation

### Purpose
Generate, validate, and classify meaningful user flows.

### Input
- `ApplicationModel`
- Device adapter (for validation execution) — optional in hypothesis-only mode

### Output
`FlowSet`

### Behaviour

1. **Hypothesis generation:** Using the world model's capabilities, screen graph, and frontier, generate candidate flows at the semantic level.

2. **Prioritisation:** Rank flow hypotheses by estimated importance and frequency.

3. **Validation** (if device adapter available): Execute each flow against the live app, recording concrete steps. Mark as validated or failed.

4. **Classification:** For validated flows, use AI to assess importance, frequency, and category.

5. **Composition detection:** Identify flows that are sub-flows of others (e.g., "login" is a sub-flow of any flow requiring authentication).

### Standalone invocation

```
pathfinder flows --model ./model.json                    # Hypotheses only
pathfinder flows --model ./model.json --validate         # With live validation
pathfinder flows --model ./model.json --validate --max-flows 20
```

Outputs a `FlowSet` as JSON.

---

## 10. Orchestrator

### Purpose
Compose the layers into a complete exploration run. Manages the exploration strategy and resource budget.

### Pipeline mode

```
Layer 0 (context) → Layer 1 (perceive all reachable screens) → Layer 2 (build model) → Layer 3 (generate flows)
```

In this mode, the orchestrator drives a systematic screen exploration in Layer 1 (using the device adapter to navigate), collects all observations, then passes them as a batch to Layer 2, then to Layer 3.

### Agent loop mode

```
while budget_remaining and not converged:
    observation = Layer1.perceive(current_screen)
    model = Layer2.update(model, [observation])
    goal = select_exploration_goal(model)  # From frontier, anomalies, or validation needs
    actions = AI.plan_exploration(model, goal)
    for action in actions:
        device.perform_action(action)
        observation = Layer1.perceive(current_screen)
        model = Layer2.update(model, [observation])
        if unexpected(observation, actions):
            break  # Re-plan
    flows = Layer3.generate(model)  # Periodic flow generation
```

### Exploration strategy

The orchestrator uses the world model's confidence and frontier to decide what to do next:

- **Low model confidence + large frontier:** Broad, shallow exploration (visit new screens)
- **High model confidence + small frontier:** Deep exploration (validate specific flows)
- **Anomalies detected:** Focus exploration on anomalous areas (novel features)
- **Expected capability absent:** Search specifically for it with decreasing priority over time

### Budget management

```python
@dataclass
class ExplorationBudget:
    max_ai_calls: int = 200
    max_actions: int = 500
    max_duration_seconds: int = 1800     # 30 minutes
    max_screens: int = 100
    convergence_threshold: float = 0.9   # Stop when model confidence reaches this
```

---

## 11. Project Structure

```
pathfinder/
├── pyproject.toml
├── README.md
├── pathfinder/
│   ├── __init__.py
│   ├── cli.py                    # CLI entry point
│   ├── contracts/                # Data structures (the inter-layer contracts)
│   │   ├── __init__.py
│   │   ├── common.py             # Provenance, ElementReference, etc.
│   │   ├── app_reference.py      # AppReference
│   │   ├── prior_context.py      # PriorContext, Capability, Entity
│   │   ├── screen_observation.py # ScreenObservation, UIElement, etc.
│   │   ├── application_model.py  # ApplicationModel, ScreenNode, etc.
│   │   └── flow.py               # Flow, FlowSet, etc.
│   ├── ai/                       # AI interface and implementations
│   │   ├── __init__.py
│   │   ├── interface.py          # AIInterface protocol
│   │   ├── config.py             # AIConfig
│   │   ├── anthropic_ai.py       # Anthropic Claude implementation
│   │   └── prompts/              # Prompt templates for each AI capability
│   │       ├── perceive.py
│   │       ├── world_model.py
│   │       ├── flow_gen.py
│   │       └── context.py
│   ├── device/                   # Device adapter and implementations
│   │   ├── __init__.py
│   │   ├── interface.py          # DeviceAdapter protocol
│   │   ├── actions.py            # DeviceAction types
│   │   └── android/              # Android implementation
│   │       ├── __init__.py
│   │       ├── adapter.py        # AndroidDeviceAdapter
│   │       ├── adb.py            # ADB wrapper
│   │       └── uiautomator.py    # uiautomator2 wrapper
│   ├── layers/                   # Layer implementations
│   │   ├── __init__.py
│   │   ├── context_gathering.py  # Layer 0
│   │   ├── perception.py         # Layer 1
│   │   ├── world_modeling.py     # Layer 2
│   │   └── flow_generation.py    # Layer 3
│   └── orchestrator/             # Orchestrator
│       ├── __init__.py
│       ├── pipeline.py           # Pipeline mode
│       ├── agent_loop.py         # Agent loop mode
│       └── strategy.py           # Exploration strategy
├── tests/
│   ├── __init__.py
│   ├── test_contracts/           # Schema validation tests
│   ├── test_layers/              # Layer unit tests
│   ├── test_ai/                  # AI interface tests (with mocks)
│   ├── test_device/              # Device adapter tests
│   └── fixtures/                 # Test screenshots, hierarchy XMLs, etc.
└── meta/                         # Design documents, transcripts
    ├── architecture.md
    └── conversation-transcript.md
```

---

## 12. MVP Scope: Layer 1 (Perception)

The first implementation milestone delivers Layer 1 end-to-end:

### What's included

1. **Contracts:** `ScreenObservation`, `UIElement`, `NavigationContext`, `PerceptionContext`, `ElementReference`, and all supporting types. Serialisable to/from JSON.

2. **AI Interface:** The `AIInterface` protocol with the `perceive` method. One concrete implementation (Anthropic Claude).

3. **Device Adapter:** The `DeviceAdapter` protocol with `get_screenshot` and `get_ui_structure`. One concrete implementation (Android via ADB + uiautomator2).

4. **Perception Layer:** The Layer 1 implementation that composes AI + device adapter to produce `ScreenObservation` from a live Android device screen.

5. **CLI:** `pathfinder perceive` command for standalone invocation.

### What's deferred

- Layer 0 (context gathering)
- Layer 2 (world modeling)
- Layer 3 (flow generation)
- Orchestrator (both modes)
- iOS and web adapters
- Alternative AI providers

### Validation

Point the system at an Android app running in an emulator. Capture a screenshot. Get back a structured `ScreenObservation` with correctly identified elements, semantic roles, screen type, and navigation context. Verify the output is valid JSON conforming to the schema.

---

## 13. Serialisation & Interoperability

All inter-layer data structures are serialisable to JSON. Each type has a `to_dict()` and `from_dict()` method. The JSON schemas are canonical — they define the contract. The Python dataclasses are one implementation of those schemas.

This means:
- Layer outputs can be saved to disk and inspected
- Layers can be invoked from the CLI with JSON file inputs
- Future implementations in other languages can interoperate by conforming to the JSON schemas
- Test fixtures are plain JSON files

---

## Appendix A: Observation Classification Logic

The three-way classification of observations relative to the world model:

| Classification | Definition | Exploration effect |
|---|---|---|
| **Confirmed** | Observation matches a prior hypothesis (expected capability found) | Decrease exploration priority for this area |
| **Novel** | Observation doesn't match any hypothesis (unexpected feature/screen) | Increase exploration priority — likely differentiating value |
| **Absent** | Expected capability/screen not found despite targeted search | Keep looking with decreasing priority; eventually mark as intentionally omitted |

---

## Appendix B: Open Design Questions

1. **Screen identity:** How to reliably determine two observations are of the "same" screen (same screen, different state vs. genuinely different screen)? Likely needs a combination of structural similarity and semantic similarity.

2. **State management:** How to handle flows that require specific preconditions (e.g., items in cart)? The system may need to perform setup actions that aren't part of the flow itself.

3. **Non-determinism:** A/B tests, network-dependent content, loading states. Defer to refinement phase or address in MVP?

4. **Credential discovery:** When the system encounters a login screen and has no credentials, how does it signal this to the user? Needs a callback/interrupt mechanism.
