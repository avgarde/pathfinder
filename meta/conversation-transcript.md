# Pathfinder — Design Conversation Transcript

## Session: 2026-04-05

---

### Turn 1 — Amit (Problem Statement)

**Problem:** Given a reference to an application (mobile Android/iOS, or web), have a computer system explore it and discover/enumerate possible paths ("flows") through the application — similar to how a human user might.

**Goals:**
- Classify flows by usefulness (accomplishing something specific) and likely frequency of use
- Build something foundational for downstream use cases: automating test suites, evaluating usability/UX
- Clear system boundary, usable representation of flows and context that can feed into other tools

**Constraints/Preferences:**
- Skeptical of brute-force enumeration: combinatorially large space, most flows uninteresting, automation mechanisms brittle, misses the essence of what's genuinely useful
- Circumspect about heavy reliance on Appium/Playwright — biases architecture toward tool capabilities rather than intent
- Want "native AI" as much as possible for understanding what an app does, what visual elements are for, and how to invoke them

---

### Turn 1 — Claude (Initial Analysis)

**Core tension identified:** Brute-force inverts the human approach — enumerates mechanisms rather than reasoning about intent.

**Foundational question:** How does an intelligent agent build a semantic model of an application from observation, and then use that model to generate meaningful flows?

**Three subproblem decomposition:**

1. **Perception** — "What am I looking at?" Understanding screen elements, their likely function, app state, and available transitions. Multimodal AI strongest here.

2. **World-modeling** — "What is this application, and what can it do?" Building/maintaining an evolving model of the app's purpose, entities, and capabilities. This is what prevents brute-force — gives hypotheses to test rather than spaces to enumerate.

3. **Flow generation** — "What are the meaningful paths?" Given the world model, generate candidate flows representing purposeful user journeys, then execute against the app to validate/refine/capture concrete steps.

**Key insight:** Layer 2 drives layer 3, layer 1 feeds layer 2. The automation layer is a servant, not the master.

**Interaction layer options proposed:**
- **Option A:** Multimodal agent with minimal automation glue (AI sees screenshots, reasons, issues high-level commands translated by thin adapter)
- **Option B:** Accessibility-tree-first with vision as fallback
- **Option C:** Hybrid with progressive depth (start with cheapest signals — app store description, docs — before touching app)
- Recommended: Combination of A and C

**Representation:** Two layers — semantic flow description (what and why) paired with concrete recorded trace (how, specifically, on this build). Downstream tools choose which layer to consume.

**Questions asked:**
1. Running app vs. app bundle/URL?
2. State and authentication bootstrapping?
3. Source of the "interesting" predicate?
4. Platform priority?
5. Implementation — large multimodal models vs. smaller specialized models?

---

### Turn 2 — Amit (Design Directions)

**Answers to questions:**

1. **Scope:** Agnostic — working from app bundle/URL is fine
2. **Auth/State:** Tool should discover these requirements, but can ask for or be supplied credentials
3. **Interesting predicate:** Some external real-world knowledge necessary; ideally the tool figures it out from a model or web search
4. **Platform priority:** Mobile first (Android → iOS), architecture should allow clean "drop-in" extensibility for web and future targets
5. **Native AI clarification:** Meant using an AI model instead of explicitly wired Appium/Playwright code. AI model use should be behind an appropriate interface — specific model, model type, external API vs. local should be configurable, but interface appears the same to the client

**Architectural requirement:** Implementation should be in layers corresponding to the three subproblems, with clear contracts/handoffs and data representation agreements between layers. Each layer's functionality should be invokeable standalone, with required input assumed available in a standardised representation (possibly as output of the preceding layer's standalone execution).

---

### Turn 2 — Claude (Design Analysis)

**On the layered independence requirement:**

Identified tension: exploration is iterative in practice (perceive → update model → hypothesize → act → perceive again), but the layering requirement implies sequential pipeline execution. Resolution proposed: support two execution modes — *pipeline mode* (run each layer to completion sequentially, for batch/testing/partial-input scenarios) and *agent loop mode* (orchestrator interleaves calls to all three layers during live exploration). Both modes consume and produce the same data formats.

**Inter-layer contracts proposed:**

1. **Layer 1 → Layer 2: "Screen Observation"**
   - Identified UI elements with inferred types and purposes (semantic, not just positional)
   - Inferred screen-level purpose
   - Navigational context (how we got here, what's reachable)
   - Detected state information
   - Design choice: push layer 1 toward semantic end (AI is already capable; deferring to layer 2 loses visual context). Semantic annotations marked as inferences, not facts.

2. **Layer 2 → Layer 3: "Application Model"**
   - App's inferred domain and purpose
   - Set of entities (products, users, orders, etc.)
   - Set of capabilities with inferred importance/frequency
   - Screen graph (discovered screens + transitions, annotated with which capabilities each participates in)
   - Exploration frontier (hypotheses about undiscovered capabilities/screens)
   - The frontier is the key differentiator from brute-force: the system hypothesizes what *should* exist.

3. **Layer 3 output: "Flow Set"**
   - Each flow has dual representation: semantic (goal, preconditions, postconditions, intent-level steps) and concrete trace (specific screens, elements, inputs, timestamps)
   - Metadata: inferred frequency, importance, category (core/edge/error), validation status (executed vs. hypothetical)

**AI interface abstraction:**
Four capability contracts: vision-to-structure, reason-over-model, generate-plan, judge. Each is a defined I/O contract; whether served by one large model or specialized models is a deployment decision behind the interface. Noted: latency differences between cloud API and local models affect agent loop tightness — architecture should support a "planning budget."

**Platform abstraction ("Device Adapter"):**
Thin interface: get_screenshot, get_structure (optional accessibility tree), perform_action (tap/type/swipe/back/scroll/wait), get_app_metadata, install_app, reset_state. Android adapter wraps ADB + UIAutomator (not Appium). iOS wraps XCTest/idb. Web wraps browser automation. No intelligence leaks upward.

**Proposed Layer 0 — Context Gathering:**
Before exploration begins, gather external context (app store description, reviews, category, help docs) to produce a prior world model. Layer 2 then refines through observation.

**Open questions:**
1. Explore-exploit tradeoff: how deeply to pursue a single flow vs. discovering new ones? Configurable or self-regulating?
2. Non-determinism: loading times, network content, A/B tests, time-dependent state — handle robustly from start or treat as refinement?

---

### Turn 2 — Amit (Continued)

Requested transcript be saved to meta directory. Confirmed design directions above and asked to proceed with analysis.

---

### Turn 3 — Amit (World Model for Novel Apps)

Agreed on: both execution modes, layer 1 leaning semantics-heavy.

Broadly agreed on the application model, but raised a key question: **How does this extend to a completely new kind of app where the model for it doesn't exist yet?** What would the model be before the first instance of an app of that type was built? Does this imply support for explicit prompting or description of the app being supplied?

---

### Turn 3 — Claude (Knowledge Hierarchy & Provenance)

**Four knowledge sources for world-modeling, in order of decreasing generality:**

1. **Universal interaction semantics** — GUI conventions (text fields afford input, buttons afford action, lists afford selection). Hold regardless of app novelty. Bedrock for perception.
2. **Compositional reasoning from structure** — inferring purpose from UI patterns ("form with fields X, Y, Z + submit button → app collects X, Y, Z"). Slower than category-matching but works for novel apps.
3. **Category-level priors** — "e-commerce apps have checkout flows." Most powerful for known categories, absent for genuinely novel apps.
4. **Explicit external knowledge** — app store descriptions, documentation, developer-supplied descriptions.

**Key insight:** These degrade gracefully. For novel apps, you lose layer 3 priors but retain layers 1-2. System goes from "I know what this should do" to "I can figure out what this does by observing carefully." Same as a human encountering a novel app.

**Provenance tagging:** Every element in the application model should be tagged with why the system believes it exists: Observed, Inferred-from-structure, Inferred-from-category, or Externally-supplied. This makes confidence levels explicit and auditable.

**Layer 0 input channels (all producing the same partial application model structure):**
1. **Automated discovery** — public sources (app store, web search, docs). Works for established apps, yields nothing for pre-release/internal.
2. **Structured description** — developer/tester supplies domain, entities, expected capabilities. For novel apps, may be the only prior.
3. **Exploratory prompts** — hints or goals ("focus on collaboration features", "novel aspect is how it handles X"). Steers exploration without requiring complete model.

All three produce the same data structure (partial, low-confidence application model). Layer 2 treats them identically.

**Adaptive exploration strategy:** When world model confidence is low (novel app), do broader/shallower sweeps to build model. When confidence is high (known category), go deeper on validating specific flows. World model confidence regulates exploration behavior.

**Asymmetry flagged:** Known apps risk overconfident priors (searching for features the developer intentionally omitted). Novel apps risk diffuse exploration approaching brute-force.

---

### Turn 4 — Amit (Category Priors Are Insufficient Even Within Known Categories)

Pointed out that even category-level priors don't completely cover a *new* app in the same general category. The differentiating features — the reason the app exists — are by definition not captured by category priors.

---

### Turn 4 — Claude (Anomaly-Driven Exploration)

**Reframed insight:** Category priors aren't just incomplete for novel apps — they're potentially *distorting* for any app that innovates within its category. System could check off standard category flows while missing the differentiating features entirely.

**World model must be generative, not a checklist.** Must recognize "this doesn't fit my model" as a signal to increase attention, not decrease it.

**Three-way observation classification added to application model:**
- **Confirmed** — matches a prior hypothesis → move on
- **Novel** — doesn't match any hypothesis → explore deeper (likely differentiating value)
- **Absent** — expected but not found → keep looking with decreasing priority

Anomalies (novel observations) become first-class objects that drive exploration priority upward.

**Refined Layer 0 structured description:** Two sections — "baseline" (what category conventions the app follows) and "differentiation" (what the app does differently). System can also infer this split by comparing observations against category priors.

**Net effect on architecture:** Same layers and contracts, but the application model's internal structure is richer — it becomes a comparative model ("how does this app relate to expectations, and where does it diverge") rather than just an inventory.

---

### Turn 5 — Amit (Proceed to Implementation)

Ready to move to architecture, design, and implementation.

---

### Turn 5 — Claude (Implementation Planning)

Asked clarifying questions:
- **Language:** Python chosen. Rationale: AI inference latency dominates, not CPU compute. Python's ecosystem advantage outweighs runtime speed. Venv setup required.
- **First output:** Design doc then code.
- **MVP scope:** Layer 1 (Perception) only.

---

### Turn 6 — Amit (Venv & GitHub Setup Requirements)

- Venv must be set up ab initio via a post-clone build script (venv files don't carry across machines)
- GitHub-ready with proper .gitignore
- Principle: commit the specification of the environment, never the environment itself

---

### Turn 6 — Claude (Implementation Complete)

**Architecture document written:** `meta/architecture.md` — comprehensive design covering all 4 layers, inter-layer contracts with Pydantic schemas, AI interface protocol, device adapter protocol, execution modes, project structure, and MVP scope definition.

**Project structure created and implemented:**

```
pathfinder/
├── pyproject.toml              # Dependencies, build config, tool config
├── .gitignore                  # Python, venv, IDE, OS, secrets
├── .env.example                # Template for API keys
├── scripts/setup.sh            # Post-clone build script (venv + deps)
├── pathfinder/
│   ├── contracts/              # Inter-layer data structures (Pydantic)
│   │   ├── common.py           # Provenance, ScreenType, ElementReference, etc.
│   │   ├── app_reference.py    # System entry point
│   │   ├── screen_observation.py  # Layer 1 output
│   │   ├── prior_context.py    # Layer 0 output
│   │   ├── application_model.py   # Layer 2 output
│   │   └── flow.py             # Layer 3 output
│   ├── ai/
│   │   ├── interface.py        # AIInterface protocol + PerceptionContext
│   │   ├── config.py           # AIConfig
│   │   ├── anthropic_ai.py     # Claude implementation
│   │   └── prompts/perceive.py # Perception prompt templates
│   ├── device/
│   │   ├── interface.py        # DeviceAdapter protocol
│   │   ├── actions.py          # DeviceAction union types
│   │   └── android/adapter.py  # ADB + uiautomator2 implementation
│   ├── layers/
│   │   └── perception.py       # Layer 1 engine (offline + live modes)
│   └── cli.py                  # CLI: perceive, perceive-live commands
└── tests/
    └── test_contracts/test_serialisation.py  # Round-trip tests (4/4 passing)
```

**Key implementation decisions:**
- All contracts use Pydantic BaseModel for automatic JSON serialisation
- AI interface is a Python Protocol (structural typing, not inheritance)
- Device adapter is also a Protocol — platform implementations are pluggable
- AnthropicAI sends screenshot as base64 image with structured prompt, parses JSON response
- Perception prompt is semantics-heavy (as agreed in design discussion)
- CLI uses Click + Rich for formatted output
- Layer 1 supports both offline (screenshot file) and live (connected device) modes

---

### Turn 7 — Amit (First Live Test — YouTube Premium Shorts)

Ran `pathfinder perceive` against a YouTube Premium Shorts screenshot. First run (pre-patch) produced good results but missed selected state on tabs.

---

### Turn 7 — Claude (Perception Quality Analysis & Schema Update)

**Analysis of first test output:**
- Correct: screen purpose, type (list), app state (logged_in, premium, current_tab)
- Correct: full element inventory including header icons, filter chips, video thumbnails, bottom nav
- Missing: no is_selected state on tabs/navigation — "Shorts" tab and "All" filter not marked as active
- Missing: no bounds/coordinates (expected without UI structure XML)

**Schema changes made:**
- Added `is_selected: bool` to `UIElement` — tracks active/selected/highlighted state
- Added `active_navigation: str | None` to `NavigationContext` — records currently selected nav element
- Updated perception prompt to explicitly instruct AI to track selected state and explain why it matters

**Second test confirmed fixes working:** "Shorts" tab → `is_selected: true`, "All" filter → `is_selected: true`, `active_navigation: "Shorts"`. Minor non-determinism noted (one video thumbnail dropped between runs — functionally equivalent items, not a concern for flow discovery but Layer 2 will need to reconcile).

---

### Turn 8 — Amit

Requested Layer 0 implementation.

---

### Turn 8 — Claude (Layer 0 Implementation)

**Implemented:**
- `pathfinder/ai/prompts/context.py` — prompt templates for context synthesis
- `pathfinder/ai/anthropic_ai.py` — added `synthesise_context` method + parser
- `pathfinder/ai/interface.py` — added `synthesise_context` to protocol
- `pathfinder/layers/context_gathering.py` — Layer 0 engine with:
  - Pluggable web search/fetch functions (injected, not hard-wired)
  - Three-phase pipeline: gather raw info → AI synthesis → enrich with provenance
  - `gather()` for full pipeline, `gather_from_description_only()` for novel/internal apps
  - JSON serialisation/deserialisation helpers
- `pathfinder/cli.py` — added `context` command

**Key design decisions:**
- Web search and fetch are injected as async callables (`WebSearchFn`, `WebFetchFn`), not hard-coded. Allows swapping implementations without changing the layer.
- CLI uses simple `curl` subprocess for web search. Production use would swap for httpx or similar.
- Works at three levels of available information: rich (public app), moderate (name + description), minimal (just a description for novel apps).
- Prompt explicitly handles baseline/differentiation distinction and instructs AI to mark differentiators with `is_differentiator: true`.

**CLI invocation:**
```
pathfinder context --name "YouTube"
pathfinder context --package "com.spotify.music" -o context.json
pathfinder context --name "MyApp" --description "..." --differentiation "..."
```

---

### Turn 9 — Amit (Layer 0 Test — Spotify)

Ran `pathfinder context --package "com.spotify.music"` and shared the output.

---

### Turn 9 — Claude (Layer 0 Quality Assessment)

**Results analysis:**
- Good: 8 capabilities correctly identified and sensibly ranked (Stream Music at 1.0/1.0, Search at 0.8/0.8)
- Good: 7 entities cover the domain well (Song, Artist, Album, Playlist, Podcast, Genre, User Profile)
- Good: category correctly identified as "music streaming"
- Missing: no differentiators flagged (expected — no `--differentiation` hint supplied)
- Note: all provenance says `automated_discovery` but is really the AI model's training knowledge since web search likely returned limited content. Provenance labelling could be more honest here.
- Note: captures category baseline well but not what's specifically Spotify (Discover Weekly, Spotify Connect, Wrapped, collaborative playlists, lyrics)

**Assessment:** Output gives Layer 2 a solid prior — it would know to look for music player, playlist management, search, podcast section, profile/settings, and the core entities.

---

### Turn 10 — Amit (Layer 0 Test — MyNovelApp)

Ran `pathfinder context --name "MyNovelApp" --description "A fitness app that uses AR to correct weightlifting form" --differentiation "Real-time AR form correction using phone camera"`. Shared output.

---

### Turn 10 — Claude (Novel App Analysis & Bug Fix)

**Results analysis:**
- Good: category correctly identified as "fitness" despite app not existing
- Good: AR form analysis ranked highest importance (0.95) — differentiating feature correctly prioritised
- Good: "Form Analysis" created as a first-class entity — app-specific concept not in generic fitness model
- Good: Standard fitness baseline correctly inferred (workout tracking, exercise library, progress monitoring)
- Bug: `is_differentiator` field was in the AI prompt schema and returned by the model, but silently dropped during parsing. The `Capability` Pydantic model lacked the field, and the parser extracted it into a variable (`is_diff`) but never used it.

**Fix applied:**
- Added `is_differentiator: bool = False` to `Capability` model
- Updated parser to pass `is_differentiator` through from AI response
- Tests still pass

---

### Turn 11 — Amit

Requested Layer 2 implementation, explanation of how layers tie together, and transcript updates.

---

### Turn 11 — Claude (Layer 2 Implementation & Integration Explanation)

**Implemented:**
- `pathfinder/ai/prompts/world_model.py` — prompts for world model update and screen identity
- `pathfinder/ai/anthropic_ai.py` — added `update_world_model` method with:
  - `_summarise_model()` — converts ApplicationModel to concise text for prompt
  - `_summarise_observations()` — converts ScreenObservations to text including selected state
  - `_summarise_prior_context()` — converts PriorContext to text
  - `_parse_world_model_response()` — parses AI response, merges with existing model (visit counts, transition counts)
- `pathfinder/ai/interface.py` — added `update_world_model` to protocol
- `pathfinder/layers/world_modeling.py` — Layer 2 engine with:
  - `create_empty_model()` — creates blank or prior-context-seeded model
  - `update()` — incremental update (agent loop mode)
  - `build_model()` — batch construction (pipeline mode, processes in batches)
  - JSON serialisation/deserialisation helpers
- `pathfinder/cli.py` — added `model` command

**CLI invocation:**
```
pathfinder model obs1.json obs2.json --prior-context context.json -o model.json
pathfinder model new_obs.json --current-model model.json -o model_v2.json
```

**Key design decisions:**
- Model updates are incremental — each update preserves existing knowledge and adds to it
- Visit counts and transition counts accumulate across updates
- The AI prompt includes full current model state + new observations, asking for a complete updated model
- Empty model can be seeded from PriorContext (frontier auto-generated from expected capabilities)
- Supports both batch mode (pipeline) and incremental mode (agent loop)

**End-to-end data flow (pipeline mode):**
```
AppReference → Layer 0 → PriorContext (.json)
                                     ↘
Screenshots → Layer 1 → ScreenObservations (.json) → Layer 2 → ApplicationModel (.json)
```
Every arrow is a JSON file. Every layer independently invocable. Intermediate files can be hand-authored to skip/override any layer.

---

### Turn 12 — Amit (Live Exploration Questions)

Three questions:
1. Will a live exploration cycle require automation to exercise app functionality?
2. Can I run what is available right now against a web target?
3. Will the agent loop make sense without the exploration automation/app exercise functionality?

---

### Turn 12 — Claude (Exploration Architecture)

**Key answers:**
1. Yes — the agent loop needs a device adapter to take actions. The layers so far are "read-only" (observe, model). The exploration loop closes the cycle: perceive → reason → **act** → repeat.
2. Not yet — need a web device adapter (Playwright-based) and the orchestrator that drives the loop.
3. Without the act step, you can run Layers 0-2 in pipeline mode but not the agent loop. The agent loop is specifically the interleaved mode that requires automation.

**Proposed plan:** Build the web adapter first (simpler than mobile for testing — no physical device needed), then the orchestrator.

---

### Turn 13 — Amit

"Build the web device adapter first, then the orchestrator."

---

### Turn 13 — Claude (Web Adapter + Orchestrator Implementation)

**Implemented:**

1. **`pathfinder/device/web/adapter.py` — WebDeviceAdapter**
   - Wraps Playwright async API for browser automation
   - Implements the same DeviceAdapter protocol as the Android adapter
   - Configurable: headless/headed, chromium/firefox/webkit, viewport size, slow_mo
   - `start()`/`stop()` lifecycle + async context manager support
   - `get_screenshot()` → Playwright's `page.screenshot()`
   - `get_ui_structure()` → JavaScript DOM walker that extracts interactive elements with bounds, ARIA labels, roles — structured like an accessibility tree
   - Element resolution via `_resolve_locator()` — tries CSS id → data-testid → aria-label → visible text (exact then partial) → role+name → bounds coordinates
   - Swipe/scroll translated to `mouse.wheel()` for web
   - `navigate_to()` convenience method for URL navigation
   - `install_app()` → navigates to web_url
   - `reset_app_state()` → clears cookies + localStorage + sessionStorage

2. **`pathfinder/ai/prompts/exploration.py` — Exploration planning prompts**
   - `EXPLORATION_PLANNER_SYSTEM_PROMPT` — instructs AI to prioritise frontier hypotheses, investigate anomalies, avoid re-treading explored screens, stop when coverage is high
   - `EXPLORATION_PLAN_SCHEMA` — JSON schema for the plan: reasoning, should_stop, action (action_type/target_text/target_role/input_text/url/direction/description), expected_outcome, exploration_goal
   - `build_exploration_plan_prompt()` — assembles current model summary, current observation summary, recent action history, and remaining budget into a prompt

3. **`pathfinder/ai/interface.py` — Extended AIInterface protocol**
   - Added `plan_exploration()` method to the Protocol
   - Added `ExplorationPlan` class to hold the AI's decision (reasoning, should_stop, action dict, expected_outcome, exploration_goal)

4. **`pathfinder/ai/anthropic_ai.py` — Added plan_exploration implementation**
   - `plan_exploration()` — sends model summary + observation to AI with exploration planner prompt
   - `_parse_exploration_plan()` — JSON parsing with graceful fallback to STOP on parse failure

5. **`pathfinder/orchestrator/agent_loop.py` — The core exploration engine**
   - `ExplorationConfig` — configures budget, output dir, what to save
   - `ExplorationStep` — records one step (observation, plan, action, result)
   - `ExplorationResult` — complete run result (model, steps, timing, stop reason)
   - `AgentLoop` — the engine itself. Composes PerceptionLayer, WorldModelingLayer, and AI exploration planning. The loop:
     1. Perceive current screen (Layer 1 live mode)
     2. Update world model with observation (Layer 2 incremental)
     3. Plan next action (exploration planner)
     4. Translate plan to DeviceAction and execute
     5. Wait for settle time, repeat
   - Stops when: AI says should_stop, action budget exhausted, or error/interrupt
   - `_translate_plan_to_action()` — converts AI plan dict into typed DeviceAction (TapAction, TypeAction, ScrollAction, etc.)
   - Handles web-specific "navigate" action type by calling `navigate_to()` directly
   - Saves screenshots, observations, model snapshots, and an exploration summary JSON

6. **`pathfinder/cli.py` — Added `explore-web` command**
   ```
   pathfinder explore-web https://news.ycombinator.com --name "Hacker News"
   pathfinder explore-web https://example.com --max-actions 30 -o ./output
   pathfinder explore-web https://app.example.com --prior-context context.json --headless
   ```
   Options: --name, --description, --max-actions, --output-dir, --headless, --browser, --prior-context, --viewport

7. **`scripts/setup.sh` — Updated**
   - Added `playwright install chromium` step after pip install
   - Added playwright to import verification

**Key design decisions:**
- The orchestrator is a composition of existing layers, not a new layer. It just sequences them in a loop.
- The exploration planner is a fourth AI capability (alongside perceive, synthesise_context, update_world_model) — it decides what to do, not what something is.
- The `_translate_plan_to_action` method bridges the semantic gap between the AI's intent ("tap the Search button") and the device adapter's input (TapAction with an ElementReference). This is where the AI's text-level description gets resolved to a concrete interaction.
- Navigation actions (URL changes) bypass the normal perform_action path since they're web-specific.
- ExplorationResult is a first-class output containing the full step history, enabling post-hoc analysis of exploration quality.

**Agent loop data flow:**
```
                    ┌─────────────────────────────────┐
                    │         Agent Loop               │
                    │                                  │
    start_url ───►  │  ┌─► Perceive (L1) ──► observe  │
                    │  │         │                     │
                    │  │         ▼                     │
                    │  │   Update Model (L2)           │
                    │  │         │                     │
                    │  │         ▼                     │
                    │  │   Plan Exploration            │
                    │  │         │                     │
                    │  │    stop? ──► yes ──► done     │
                    │  │         │ no                  │
                    │  │         ▼                     │
                    │  │   Execute Action              │
                    │  │         │                     │
                    │  └─────────┘                     │
                    │                                  │
                    │  Output: ApplicationModel        │
                    │        + ExplorationResult       │
                    └─────────────────────────────────┘
```

**Running it:**
```bash
# From your machine (with .env containing ANTHROPIC_API_KEY):
source .venv/bin/activate
pathfinder explore-web https://news.ycombinator.com --name "Hacker News" --max-actions 10
```
This will open a Chromium window, navigate to the URL, and start the autonomous perceive→model→plan→act cycle. Screenshots and the final model are saved to `./exploration/`.

---
