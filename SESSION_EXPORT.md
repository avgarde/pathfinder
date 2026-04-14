# Pathfinder Session Export
*Last updated: 2026-04-14 (Session 5 — AI-driven generate strategy implemented)*

Complete project context for reconstructing the Pathfinder exploration engine in a new Claude session.

---

## 1. PROJECT OVERVIEW

### What is Pathfinder

Pathfinder is an AI-driven autonomous application explorer that discovers meaningful user flows through web and mobile applications. It works by:

1. **Perceiving** the current screen (AI-powered semantic analysis)
2. **Building a world model** of the app (screens, transitions, capabilities, entities)
3. **Planning exploration** (deciding what to interact with next)
4. **Generating flows** (identifying coherent user journeys)

### Product Objective (clarified in Session 3)

Pathfinder is the **discovery pass** in a two-phase system:

- **Phase 1 (Discover)**: Pathfinder autonomously explores an app, building an ApplicationModel and generating candidate FlowSets
- **Phase 2 (Verify)**: A DragonCrawl-style verifier executes each candidate flow goal-directed, checking assertions at every step, producing validated flow artifacts
- **Phase 3 (Report)**: Generates downstream artifacts from verified flows: Playwright test files, telemetry event schemas, usability heuristic reports, requirement coverage reports

The primary outputs feed four downstream use cases:
1. **QA / Testing** — reproducible, executable, assertion-backed test cases
2. **Usability assessment** — friction scores, path quality, failure states
3. **Spec compliance** — traceability from discovered flow to requirement
4. **Telemetry instrumentation** — stable semantic event anchors

### The User

**Amit Garde** — Prefers first-principles reasoning, building coherent mental models, and reasoning from foundational axioms. Values clean architecture and design clarity.

### Project Location

```
/Users/amitgarde/hacking/claude-code/pathfinder/
```

Uses a Python virtual environment at `.venv/` in the project root.

---

## 2. ARCHITECTURE

### Four-Layer Design (unchanged from Session 1)

1. **L0: Context Gathering** — External knowledge synthesis → `PriorContext`
2. **L1: Perception** — Screenshot analysis + accessibility tree → `ScreenObservation`
3. **L2: World Modeling** — Screen graph construction → `ApplicationModel`
4. **L3: Flow Generation** — User journey identification → `FlowSet`

### New Components (added in Session 3 or planned)

5. **FlowVerifier** (Phase 3 — planned) — Executes candidate flows goal-directed, checks assertions per step → `VerifiedFlowSet`
6. **ReportGenerator** (Phase 4 — planned) — Transforms verified flows into downstream artifacts

### Core Components (unchanged)

- **AIInterface Protocol** — provider-agnostic, 5 async methods
- **AnthropicAI** — now uses `AsyncAnthropic` (fixed in Session 3)
- **OpenAICompatibleAI** — streaming mode
- **WebDeviceAdapter** — Playwright-based web automation
- **AndroidDeviceAdapter** — ADB-based Android automation
- **EventBus** — frozen dataclasses, async ordered dispatch
- **AgentLoop** — main orchestrator, now accepts `stop_flag: asyncio.Event`
- **WebSocket Server** — IDE bridge
- **Pathfinder Studio** — Electron IDE

---

## 3. FIXES IMPLEMENTED IN SESSION 3

All 7 fixes were applied and verified. Here is the canonical list with file locations.

### Fix 1: Async Anthropic client
**File**: `pathfinder/ai/anthropic_ai.py`
- Changed `anthropic.Anthropic` → `anthropic.AsyncAnthropic` in `__init__`
- Added `await` to all 5 `self.client.messages.create()` calls
- Fixes: event loop blocked for every API call (10–30s per call)

### Fix 2: Stop flag in agent loop
**Files**: `pathfinder/orchestrator/agent_loop.py`, `pathfinder/server.py`
- `AgentLoop.__init__` now accepts `stop_flag: asyncio.Event | None = None`
- Main exploration loop checks flag at top of every iteration; breaks gracefully
- Server passes `self._stop_flag` when constructing AgentLoop
- Fixes: "Stop" button in IDE had no effect

### Fix 3: Structural coverage estimation
**File**: `pathfinder/ai/anthropic_ai.py` (`_parse_world_model_response`)
- Computes `structural_coverage = confirmed_screens / (confirmed_screens + frontier_items)`
- Blends with LLM estimate: `max(structural, min(ai_estimate, structural + 0.15))`
- Fixes: coverage_estimate was purely LLM self-assessment (unreliable)

### Fix 4: Screen fingerprinting for deduplication
**Files**: `pathfinder/contracts/application_model.py`, `pathfinder/ai/anthropic_ai.py`
- `ScreenNode` now has `fingerprint: str = ""` field
- Fingerprint = `"{screen_type}:{normalized_name}:{first_5_purpose_words}"`
- On world model update, screens with matching fingerprint are merged (visit count incremented) rather than duplicated
- Fixes: screen count inflation from inconsistent LLM screen IDs

### Fix 5: World model compression
**File**: `pathfinder/ai/anthropic_ai.py` (`_summarise_model`)
- `COMPRESSION_THRESHOLD = 8` — above this, tiered summary format
- Old confirmed screens: one-liner `[id] name (type) visits=N`
- Recent 3 screens: full detail
- Transitions: capped at last 20
- Fixes: world model prompt grew unboundedly with exploration depth

### Fix 6: Graph-level cycle detection
**File**: `pathfinder/orchestrator/agent_loop.py`
- `_recent_fingerprints: list[str]` — sliding window of last 8 screen fingerprints
- Detects 2-screen cycles (A→B→A→B) and 3-screen cycles (A→B→C→A→B→C)
- Both types: marks screens as dead ends, injects override warning to planner, triggers forced-back
- Extends the stuck override check to cover cycle cases
- Fixes: exploration trapped in multi-screen loops that same-screen detection missed

### Fix 7: prior_screen in perception prompt
**File**: `pathfinder/ai/prompts/perceive.py`
- `build_perception_prompt` now includes "Previous screen" section when `context.prior_screen` is set
- Tells model what it just came from for better navigation context inference
- The field was already populated in agent_loop.py; it just wasn't in the prompt

---

## 4. BENCHMARK SUITE (created in Session 3)

Location: `tests/benchmarks/`

### Files

- `benchmark_suite.py` — 6 benchmark definitions
- `evaluator.py` — `evaluate_run()` and `compare_runs()` for A/B testing
- `metrics.py` — `RunMetrics.from_run_dir()` extracts 20 objective metrics
- `run_benchmarks.py` — CLI: `list`, `run`, `evaluate` commands
- `README.md` — documentation

### Six Benchmarks

| ID | Site | Purpose |
|----|------|---------|
| `hacker-news` | news.ycombinator.com | Baseline — simplest stable site |
| `books-to-scrape` | books.toscrape.com | Ground-truth e-commerce (purpose-built for testing) |
| `tosdr` | tosdr.org | Directory + search |
| `wikipedia` | en.wikipedia.org | Deep navigation, search |
| `github-explore` | github.com/explore | Complex SPA, partial auth wall |
| `cricinfo` | espncricinfo.com | Heavy SPA, real-world sports site |

### Usage

```bash
python -m tests.benchmarks.run_benchmarks list
python -m tests.benchmarks.run_benchmarks run hacker-news
python -m tests.benchmarks.run_benchmarks evaluate --run-dir ./exploration/xyz/ --benchmark hacker-news
```

---

## 5. CRITIQUE AND IMPROVEMENT PLAN (Session 3)

### The Core Problem

The `Flow` contract is too generic to be an operational artifact. Flows are currently "AI-authored flow notes" not "usable system artifacts." Specifically:

- `preconditions`/`postconditions` are plain strings, not machine-checkable
- No success criteria tied to observable UI state
- No assertions per step (only `expected_outcome: str`)
- No failure branches or alternate paths
- No per-step confidence or evidence traceability
- No downstream mappings (test cases, telemetry events, requirements)
- `validation_status="validated"` means "AI thinks it observed this" not "execution confirmed"

### The Phased Improvement Plan

#### Phase 1: Redesign the Flow artifact (IN PROGRESS)

New/changed contracts in `pathfinder/contracts/flow.py`:

**New: `StepAssertion`** — verifiable condition after a step
```
assertion_id, description, assertion_type (screen_type_is | element_visible |
element_text_contains | state_changed | url_contains | semantic),
target, expected_value, confidence
```

**New: `StepEvidence`** — links step to specific observation
```
observation_id, screenshot_path, relevant_element_ids, ai_confidence, execution_verified
```

**New: `FlowStep`** (replaces SemanticStep + ConcreteStep split)
```
step_number, intent, screen_context, semantic_target, action_type,
action_target_text, action_input_value, expected_outcome,
assertions: list[StepAssertion], evidence: StepEvidence | None,
confidence, is_friction_point, friction_reason, prerequisite_state
```

**New: `EntryCondition`** (replaces string preconditions)
```
description, condition_type (app_state | screen_type | authenticated |
unauthenticated | input_available | prerequisite_flow),
key, value, flow_id
```

**New: `FlowBranch`** — alternate paths and failure modes
```
branch_id, description, branch_type (prerequisite_missing | error_state |
alternate_path | optional_detour), trigger_condition, diverges_at_step,
steps: list[FlowStep], reconnects_at_step, resolves_to_flow
```

**New: `TelemetryEventCandidate`**
```
event_name, trigger_description, suggested_properties, step_number
```

**New: `DownstreamMappings`**
```
test_assertions, test_preconditions, requirement_ids,
telemetry_events: list[TelemetryEventCandidate], funnel_name, funnel_step_names
```

**Enriched: `Flow`** — major changes
```
# New fields added:
description: str
entry_conditions: list[EntryCondition]   # replaces preconditions
required_inputs: list[str]
success_criteria: list[str]
exit_state: dict[str, str]
steps: list[FlowStep]                    # replaces semantic_steps + concrete_steps
branches: list[FlowBranch]
friction_score: float | None
evidence_strength: float
validation_status: candidate | validated | partial | failed | blocked
validation_run_id, last_validated_at, execution_duration_ms
downstream: DownstreamMappings
related_flows, prerequisite_flows, sub_flows
```

Also update flow generation prompt (`prompts/flow_generation.py`) to produce richer format,
and update `_parse_flows` in `layers/flow_generation.py` and `_parse_flow_generation_response`
in `ai/anthropic_ai.py` / `ai/openai_compatible.py`.

#### Phase 2: Goal-directed exploration

- Add `exploration_goals: list[str]` to `ExplorationConfig`
- Add `confirmed_goals: set[str]` tracking in agent loop
- Planner receives unconfirmed goals and prioritizes toward them
- Stop condition: all goals confirmed OR frontier exhausted OR budget exhausted
- Complete credential supply path: server's `_cmd_start_exploration` InputSpec loading stub needs to be implemented
- `generate` strategy needs to actually call AI to synthesize value

#### Phase 3: FlowVerifier (DragonCrawl-style)

New component: `pathfinder/verifier/flow_verifier.py`

```python
class FlowVerifier:
    async def verify_flow(self, flow: Flow, start_url, input_registry) -> VerificationResult
    async def verify_all(self, flow_set: FlowSet, ...) -> VerifiedFlowSet
```

Per step:
1. Check entry conditions
2. Semantic element matching (embed semantic_target, match against screen elements)
3. Execute action
4. Run StepAssertions (VQA or structural checks)
5. Populate StepEvidence with execution_verified=True

New CLI: `pathfinder verify --flows flows.json --url https://app.com`
Regression mode: `pathfinder verify --flows flows_verified.json --regression` (CI hook, exit 1 on failure)

#### Phase 4: Downstream artifact generation

New CLI subcommands under `pathfinder report`:

- `pathfinder report test-cases --flows flows_verified.json --format playwright`
  → `.spec.ts` files with semantic selectors (`getByRole`, `getByLabel`, regex)
- `pathfinder report telemetry --flows flows_verified.json`
  → JSON event schemas with canonical `{flow}.{step_verb}` event names
- `pathfinder report coverage --flows flows_verified.json --requirements requirements.json`
  → Requirement coverage matrix
- `pathfinder report usability --flows flows_verified.json`
  → Friction scores, path length analysis, abandoned branches

#### Phase 5: Combined workflow

```bash
pathfinder run https://app.com \
  --name "My App" \
  --goals "find checkout,find account management" \
  --inputs inputs.json \
  --max-actions 60 \
  --report all
```

Chains: discover → verify → report. Exit 0 if all verified flows pass.

---

## 6. CONFIGURATION AND ENVIRONMENT

### Environment Variables

```
PATHFINDER_AI_PROVIDER          "anthropic" (default) or "openai_compatible"
ANTHROPIC_API_KEY               Required for provider=anthropic
PATHFINDER_AI_MODEL             Model name (default: "claude-sonnet-4-20250514")
PATHFINDER_AI_BASE_URL          Base URL for OpenAI-compatible endpoints
PATHFINDER_AI_API_KEY           API key for non-Anthropic providers
PATHFINDER_AI_MAX_TOKENS        Default 4096
PATHFINDER_AI_TIMEOUT           HTTP timeout in seconds (default 600 = 10 min)
```

### .env File Support

Both CLI and server read `.env` from current working directory.

---

## 7. CLI COMMANDS

### Current (all working)

```
pathfinder perceive <screenshot>              Analyse single screenshot offline
pathfinder update-model <screenshot>          Update world model with new observation
pathfinder plan --current-model FILE          Get exploration plan from AI
pathfinder explore-web <url> --name NAME      Primary command: full exploration
pathfinder generate-flows --model FILE        Generate flows from saved model+trace
pathfinder visualise <run-dir>                Generate flows_visual.html
pathfinder serve [--host HOST] [--port PORT]  Start WebSocket server for IDE
```

### Added in Session 4 (all implemented)

```
pathfinder verify --flows FILE --url URL      Execute and validate candidate flows
  --regression                                  Exit 1 if previously-validated flow regresses
  --no-ai-assertions                            Disable AI semantic assertion evaluation

pathfinder report test-cases --flows FILE     Generate Playwright TypeScript .spec.ts files
  --format playwright|json                      Output format

pathfinder report telemetry --flows FILE      Generate JSON telemetry event schema

pathfinder report coverage --flows FILE       Requirement traceability Markdown matrix
  --requirements FILE                           JSON mapping req_id → description

pathfinder report usability --flows FILE      Friction, path length, branching analysis

pathfinder run <url>                          Full pipeline: discover → verify → report
  --goals "find checkout,find settings"         Exploration goals
  --inputs FILE                                 Credentials/inputs JSON
  --max-actions N                               Exploration budget
  --report all|test-cases,telemetry,...         Which reports to generate
  --skip-verify                                 Skip verification pass
  --regression                                  Exit 1 on verified flow regressions
```

---

## 8. HOW TO RUN

### CLI Mode

```bash
cd /Users/amitgarde/hacking/claude-code/pathfinder
source .venv/bin/activate
pip install -e .
pathfinder explore-web https://example.com --name "Example" --max-actions 50
open ./exploration/*/flows_visual.html
```

### Server + IDE Mode

```bash
# Terminal 1
pathfinder serve --port 9720

# Terminal 2
cd pathfinder-studio && npm install && npm start
```

### Using Local Models

```bash
export PATHFINDER_AI_PROVIDER=openai_compatible
export PATHFINDER_AI_BASE_URL=http://localhost:11434/v1
export PATHFINDER_AI_MODEL=qwen2.5-vl:32b
pathfinder explore-web https://example.com --name "Test" --max-actions 30
```

---

## 9. KEY DESIGN DECISIONS

### Frozen dataclasses for events
Immutability prevents accidental state mutation in long-running servers. `.to_dict()` is explicit and auditable.

### Provider-agnostic AIInterface Protocol
Structural `Protocol` (not ABC) means implementations are interchangeable without inheritance coupling.

### Stop flag via asyncio.Event (not cancellation)
Graceful stop allows the run to save its artifacts before exiting. `task.cancel()` is the fallback after 5s timeout.

### Structural coverage estimation
`confirmed / (confirmed + frontier)` is ground truth. LLM self-estimate is blended in as a small upward adjustment only.

### Screen fingerprinting for dedup
`screen_type:normalized_name:purpose_words` is stable across AI rewording but distinguishes structurally different screens.

### FlowStep unification (Phase 1)
Replacing the SemanticStep/ConcreteStep split with a single FlowStep acknowledges that every step should eventually have both semantic intent AND execution evidence. The split implied they were separate concerns; they're not.

### Candidate → Validated lifecycle
Phase 1 (exploration) produces candidates. Phase 2 (verification) promotes them to validated. This is the key architectural insight from the DragonCrawl synthesis. A flow should never be called "validated" unless it was actually executed.

---

## 10. KNOWN ISSUES AND STATUS

### Fixed in Session 3
- ~~Sync Anthropic client blocking event loop~~ (Fix 1)
- ~~Stop flag not checked in agent loop~~ (Fix 2)
- ~~Coverage estimate unreliable~~ (Fix 3)
- ~~Screen count inflation from LLM ID inconsistency~~ (Fix 4)
- ~~World model prompt growing unboundedly~~ (Fix 5)
- ~~No multi-screen cycle detection~~ (Fix 6)
- ~~prior_screen not used in perception prompt~~ (Fix 7)

### Implemented in Session 4
- ✓ Flow contract redesigned (Phase 1) — FlowStep, StepAssertion, EntryCondition, FlowBranch, TelemetryEventCandidate, DownstreamMappings
- ✓ Goal-directed exploration (Phase 2) — ExplorationConfig.exploration_goals, CLI --goals flag, confirmed_goals tracking
- ✓ FlowVerifier (Phase 3) — pathfinder/verifier/flow_verifier.py, CLI: pathfinder verify
- ✓ ReportGenerator (Phase 4) — pathfinder/reporter/report_generator.py, CLI: pathfinder report
- ✓ Combined pipeline (Phase 5) — CLI: pathfinder run → discover+verify+report

### Implemented in Session 5
- ✓ AI-driven `generate` strategy in `InputRegistry.async_resolve()` — calls AI client to synthesise a realistic sample value from field name, label, placeholder, category, screen purpose, and hint; caches result per run; falls back to hint if AI unavailable
- ✓ `FlowVerifier._execute_step_action` now uses `await input_registry.async_resolve(...)` for `type` actions, passing flow goal + step intent as context

### Remaining
- Android screen mirroring not wired in Studio
- Transition edges not drawn in graph visualization (only nodes)
- Session export/resume not implemented

### Known Intermittent
- Blank screenshot on heavy JS SPAs (retried once, then proceeds)
- Navigation timeout on very slow sites (logged as warning, continues)

---

## 11. FILE STRUCTURE

```
pathfinder/
├── pathfinder/
│   ├── __init__.py
│   ├── cli.py                    (~1700 lines — includes verify, report, run commands)
│   ├── events.py                 (EventBus + all event types)
│   ├── server.py                 (WebSocket server)
│   ├── visualise.py              (HTML flow visualization — updated for v2 steps)
│   ├── verifier/
│   │   └── flow_verifier.py      (FlowVerifier — Phase 3)
│   ├── reporter/
│   │   └── report_generator.py   (ReportGenerator — Phase 4)
│   ├── ai/
│   │   ├── interface.py          (AIInterface Protocol, ExplorationPlan)
│   │   ├── config.py             (AIConfig)
│   │   ├── anthropic_ai.py       (~724 lines, AsyncAnthropic)
│   │   ├── openai_compatible.py  (~708 lines, streaming)
│   │   └── prompts/
│   │       ├── perceive.py       (L1 prompt, now uses prior_screen)
│   │       ├── world_model.py    (L2 prompt)
│   │       ├── exploration.py    (planner prompt)
│   │       ├── flow_generation.py (L3 prompt — being redesigned Phase 1)
│   │       └── context.py        (L0 prompt)
│   ├── contracts/
│   │   ├── __init__.py
│   │   ├── app_reference.py
│   │   ├── application_model.py  (ScreenNode now has fingerprint field)
│   │   ├── common.py             (ScreenType, FlowCategory, ElementReference)
│   │   ├── flow.py               (BEING REDESIGNED — Phase 1)
│   │   ├── inputs.py             (InputSpec, InputRegistry)
│   │   ├── prior_context.py      (PriorContext, Capability, Entity)
│   │   └── screen_observation.py (ScreenObservation, UIElement)
│   ├── device/
│   │   ├── actions.py            (TapAction, TypeAction, etc.)
│   │   ├── interface.py          (DeviceAdapter Protocol)
│   │   ├── web/adapter.py        (~579 lines, Playwright)
│   │   └── android/adapter.py   (~316 lines, ADB)
│   ├── layers/
│   │   ├── perception.py         (PerceptionLayer)
│   │   ├── world_modeling.py     (WorldModelingLayer)
│   │   ├── flow_generation.py    (FlowGenerationLayer — being updated Phase 1)
│   │   └── context_gathering.py  (ContextGatheringLayer)
│   └── orchestrator/
│       └── agent_loop.py         (~952 lines, now has stop_flag + cycle detection)
├── tests/
│   ├── test_contracts/
│   │   └── test_serialisation.py (4 round-trip tests)
│   └── benchmarks/               (NEW — Session 3)
│       ├── __init__.py
│       ├── benchmark_suite.py    (6 benchmark definitions)
│       ├── evaluator.py          (evaluate_run, compare_runs)
│       ├── metrics.py            (RunMetrics.from_run_dir)
│       ├── run_benchmarks.py     (CLI tool)
│       └── README.md
├── pathfinder-studio/             (Electron IDE)
│   ├── main.js
│   ├── renderer/
│   │   ├── index.html
│   │   ├── styles.css
│   │   └── app.js
│   └── package.json
├── exploration/                   (run outputs — not in git)
├── pyproject.toml
└── SESSION_EXPORT.md              (this file)
```

---

## 12. GIT STATE

Three commits as of Session 2:
1. `25c4eb1` — Pathfinder CLI v1
2. `0886f7f` — Add Pathfinder Studio
3. `6814ce8` — Fix event field name mismatches

Session 3 changes (Fixes 1–7 + benchmarks + Phase 1) need to be committed.

---

## 13. INPUT GENERATION DESIGN (Session 5)

### `InputRegistry.async_resolve(field, context, ai, request)`

Located in `pathfinder/contracts/inputs.py`. The async counterpart of `resolve()` that properly synthesises values for the `"generate"` strategy.

**Signature**:
```python
async def async_resolve(
    self,
    field: str,
    context: str = "",
    ai: Any | None = None,
    request: InputRequest | None = None,
) -> str | None
```

**Behaviour per strategy**:
- `"literal"` / `"skip"` / `"ask"` → delegates to synchronous `resolve()`
- `"generate"` → builds a rich prompt from: field name, element label, placeholder, category, screen purpose, and generate_hint; calls `ai.client.messages.create(max_tokens=64)` to produce a single realistic value; caches by `(field, hint, context)` so the same field returns the same value within a run; falls back to `hint or "sample_{field}"` if AI call fails or `ai` is None

**Integration point**:
`FlowVerifier._execute_step_action` (type action path) calls:
```python
resolved = await input_registry.async_resolve(
    field=step.input_field,
    context=f"Flow: {self._current_flow_goal}\nStep {step.step_number}: {step.intent}",
    ai=self.ai,
)
```

`self._current_flow_goal` is set at the top of `verify_flow()` so it's available to helpers.

**Files modified in Session 5**:
- `pathfinder/contracts/inputs.py` — `async_resolve` added to `InputRegistry`
- `pathfinder/verifier/flow_verifier.py` — `_execute_step_action` uses `await async_resolve`; `_current_flow_goal` instance var added

---

**End of Session Export — Session 5 (2026-04-14)**
