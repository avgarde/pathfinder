"""Pathfinder CLI entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from pathfinder import __version__

console = Console()


def setup_logging(verbose: bool) -> None:
    """Configure logging with rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _load_env_file() -> dict[str, str]:
    """Load key=value pairs from .env if it exists."""
    env_path = Path.cwd() / ".env"
    result: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def get_ai_config() -> "AIConfig":
    """Build AIConfig from environment variables.

    Provider selection (PATHFINDER_AI_PROVIDER):
      "anthropic"          — Anthropic Claude API (default)
      "openai_compatible"  — Any OpenAI-compatible endpoint

    Environment variables:
      ANTHROPIC_API_KEY     — API key for Anthropic (required for provider=anthropic)
      PATHFINDER_AI_PROVIDER — "anthropic" or "openai_compatible"
      PATHFINDER_AI_MODEL    — Model name (default varies by provider)
      PATHFINDER_AI_BASE_URL — Base URL for the API endpoint
                               (e.g., http://localhost:11434/v1 for Ollama)
      PATHFINDER_AI_API_KEY  — API key for non-Anthropic providers
      PATHFINDER_AI_MAX_TOKENS — Max tokens for responses (default: 4096)
    """
    from pathfinder.ai.config import AIConfig

    env_file = _load_env_file()

    def env(key: str) -> str | None:
        """Check os.environ first, then .env file."""
        return os.environ.get(key) or env_file.get(key)

    provider = env("PATHFINDER_AI_PROVIDER") or "anthropic"

    if provider == "anthropic":
        api_key = env("ANTHROPIC_API_KEY")
        if not api_key:
            console.print(
                "[red]Error:[/red] ANTHROPIC_API_KEY not set. "
                "Set it in your environment or in a .env file.\n"
                "Or set PATHFINDER_AI_PROVIDER=openai_compatible to use a local model."
            )
            sys.exit(1)

        return AIConfig(
            provider="anthropic",
            api_key=api_key,
            model=env("PATHFINDER_AI_MODEL") or "claude-sonnet-4-20250514",
            base_url=env("PATHFINDER_AI_BASE_URL"),
            max_tokens=int(env("PATHFINDER_AI_MAX_TOKENS") or "4096"),
        )

    elif provider == "openai_compatible":
        base_url = env("PATHFINDER_AI_BASE_URL")
        if not base_url:
            console.print(
                "[red]Error:[/red] PATHFINDER_AI_BASE_URL not set. "
                "For Ollama: http://localhost:11434/v1\n"
                "For vLLM: http://localhost:8000/v1\n"
                "For LM Studio: http://localhost:1234/v1"
            )
            sys.exit(1)

        return AIConfig(
            provider="openai_compatible",
            api_key=env("PATHFINDER_AI_API_KEY") or "not-needed",
            model=env("PATHFINDER_AI_MODEL") or "qwen2.5-vl:32b",
            base_url=base_url,
            max_tokens=int(env("PATHFINDER_AI_MAX_TOKENS") or "4096"),
            timeout=float(env("PATHFINDER_AI_TIMEOUT") or "600"),
        )

    else:
        console.print(
            f"[red]Error:[/red] Unknown provider '{provider}'. "
            "Supported: anthropic, openai_compatible"
        )
        sys.exit(1)


def get_ai(config: "AIConfig"):
    """Instantiate the right AI backend based on config.provider."""
    if config.provider == "openai_compatible":
        from pathfinder.ai.openai_compatible import OpenAICompatibleAI
        return OpenAICompatibleAI(config)
    else:
        from pathfinder.ai.anthropic_ai import AnthropicAI
        return AnthropicAI(config)


@click.group()
@click.version_option(version=__version__)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging")
def main(verbose: bool) -> None:
    """Pathfinder: AI-driven application flow discovery and exploration."""
    setup_logging(verbose)


@main.command()
@click.argument("screenshot", type=click.Path(exists=True))
@click.option(
    "--ui-structure",
    type=click.Path(exists=True),
    help="Accessibility tree XML file",
)
@click.option(
    "--context-json",
    type=click.Path(exists=True),
    help="Perception context JSON file",
)
@click.option(
    "--domain",
    type=str,
    help="Known app domain (e.g., 'e-commerce', 'social media')",
)
@click.option(
    "--output", "-o",
    type=click.Path(),
    help="Output JSON file (default: stdout)",
)
def perceive(
    screenshot: str,
    ui_structure: str | None,
    context_json: str | None,
    domain: str | None,
    output: str | None,
) -> None:
    """Analyse a screenshot and produce a structured ScreenObservation.

    SCREENSHOT is the path to an app screenshot image (PNG, JPG).

    Examples:

        pathfinder perceive screen.png

        pathfinder perceive screen.png --ui-structure hierarchy.xml

        pathfinder perceive screen.png --domain "e-commerce" -o observation.json
    """
    asyncio.run(_perceive_async(screenshot, ui_structure, context_json, domain, output))


async def _perceive_async(
    screenshot: str,
    ui_structure: str | None,
    context_json: str | None,
    domain: str | None,
    output: str | None,
) -> None:
    """Async implementation of the perceive command."""
    from pathfinder.ai.interface import PerceptionContext
    from pathfinder.layers.perception import PerceptionLayer

    config = get_ai_config()
    ai = get_ai(config)
    layer = PerceptionLayer(ai=ai)

    # Build context if any context options provided
    context = None
    if context_json:
        ctx_data = json.loads(Path(context_json).read_text())
        context = PerceptionContext(
            known_domain=ctx_data.get("known_domain"),
            known_entities=ctx_data.get("known_entities"),
            exploration_focus=ctx_data.get("exploration_focus"),
        )
    elif domain:
        context = PerceptionContext(known_domain=domain)

    # Read UI structure if provided
    ui_xml = None
    if ui_structure:
        ui_xml = Path(ui_structure).read_text()

    # Run perception
    console.print(f"[bold]Analysing:[/bold] {screenshot}")
    observation = await layer.perceive_screenshot(
        screenshot_path=screenshot,
        ui_structure_xml=ui_xml,
        context=context,
    )

    # Output
    json_output = observation.model_dump_json(indent=2, exclude={"raw_ai_response"})

    if output:
        Path(output).write_text(json_output)
        console.print(f"[green]Observation saved to:[/green] {output}")
    else:
        console.print()
        console.print(Panel(f"[bold]{observation.screen_purpose}[/bold]", title="Screen Purpose"))

        # Summary table
        table = Table(title="Screen Analysis")
        table.add_column("Property", style="cyan")
        table.add_column("Value")
        table.add_row("Type", observation.screen_type.value)
        table.add_row("Confidence", f"{observation.confidence:.0%}")
        table.add_row("Interactive Elements", str(
            sum(1 for e in observation.elements if e.is_interactive)
        ))
        table.add_row("Total Elements", str(len(observation.elements)))
        if observation.app_state:
            table.add_row("App State", ", ".join(
                f"{k}={v}" for k, v in observation.app_state.items()
            ))
        if observation.navigation_context.visible_navigation:
            table.add_row("Navigation", ", ".join(
                observation.navigation_context.visible_navigation
            ))
        console.print(table)

        # Elements
        if observation.elements:
            console.print()
            elem_table = Table(title="UI Elements")
            elem_table.add_column("#", style="dim")
            elem_table.add_column("Type", style="cyan")
            elem_table.add_column("Label")
            elem_table.add_column("Role")
            elem_table.add_column("Interactive", justify="center")
            for elem in observation.elements:
                elem_table.add_row(
                    elem.element_id,
                    elem.element_type,
                    elem.label[:40] if elem.label else "",
                    elem.semantic_role[:50] if elem.semantic_role else "",
                    "Yes" if elem.is_interactive else "",
                )
            console.print(elem_table)

        # Also print raw JSON
        console.print()
        console.print("[dim]Full JSON output:[/dim]")
        console.print(json_output)


@main.command()
@click.option("--serial", "-s", type=str, help="Android device serial")
@click.option("--output-dir", "-o", type=click.Path(), default=".", help="Output directory")
@click.option("--domain", type=str, help="Known app domain")
def perceive_live(
    serial: str | None,
    output_dir: str,
    domain: str | None,
) -> None:
    """Capture and analyse the current screen from a connected Android device."""
    asyncio.run(_perceive_live_async(serial, output_dir, domain))


async def _perceive_live_async(
    serial: str | None,
    output_dir: str,
    domain: str | None,
) -> None:
    """Async implementation of perceive-live."""
    from pathfinder.ai.interface import PerceptionContext
    from pathfinder.device.android.adapter import AndroidDeviceAdapter
    from pathfinder.layers.perception import PerceptionLayer

    config = get_ai_config()
    ai = get_ai(config)
    device = AndroidDeviceAdapter(serial=serial)
    layer = PerceptionLayer(ai=ai, device=device)

    context = PerceptionContext(known_domain=domain) if domain else None

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    console.print("[bold]Capturing from device...[/bold]")
    observation = await layer.perceive_live(
        output_dir=output_dir,
        context=context,
    )

    # Save observation
    obs_path = str(Path(output_dir) / "observation.json")
    PerceptionLayer.save_observation(observation, obs_path)
    console.print(f"[green]Observation saved to:[/green] {obs_path}")
    console.print(f"[bold]Screen:[/bold] {observation.screen_purpose}")
    console.print(f"[bold]Type:[/bold] {observation.screen_type.value}")
    console.print(f"[bold]Elements:[/bold] {len(observation.elements)}")


@main.command()
@click.option("--name", type=str, help="App name (e.g., 'Spotify')")
@click.option("--package", type=str, help="Package name (e.g., 'com.spotify.music')")
@click.option("--app-store-url", type=str, help="App store URL")
@click.option("--description", type=str, help="User-supplied app description")
@click.option("--baseline", type=str, help="What's standard about this app")
@click.option("--differentiation", type=str, help="What's novel about this app")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file (default: stdout)")
def context(
    name: str | None,
    package: str | None,
    app_store_url: str | None,
    description: str | None,
    baseline: str | None,
    differentiation: str | None,
    output: str | None,
) -> None:
    """Gather external context about an app and produce a PriorContext.

    Searches the web for app information, then uses AI to synthesise
    a structured understanding of the app's capabilities and entities.

    Examples:

        pathfinder context --name "Spotify"

        pathfinder context --package "com.spotify.music" -o context.json

        pathfinder context --name "MyApp" --description "A fitness app" --differentiation "Novel AI coaching"
    """
    if not any([name, package, app_store_url, description]):
        console.print("[red]Error:[/red] Provide at least one of --name, --package, --app-store-url, or --description")
        sys.exit(1)

    asyncio.run(_context_async(name, package, app_store_url, description, baseline, differentiation, output))


async def _context_async(
    name: str | None,
    package: str | None,
    app_store_url: str | None,
    description: str | None,
    baseline: str | None,
    differentiation: str | None,
    output: str | None,
) -> None:
    """Async implementation of the context command."""
    import subprocess

    from pathfinder.contracts.app_reference import AppReference
    from pathfinder.layers.context_gathering import ContextGatheringLayer

    config = get_ai_config()
    ai = get_ai(config)

    # Build web search/fetch functions using subprocess calls to curl
    # These are simple implementations; production would use httpx
    async def web_search(query: str) -> str:
        """Simple web search via Google (returns raw HTML)."""
        import urllib.parse
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "-A", "Mozilla/5.0", url],
                capture_output=True, timeout=15, check=False,
            )
            return result.stdout.decode(errors="replace")[:10000]
        except Exception as e:
            logger.warning("Web search failed: %s", e)
            return ""

    async def web_fetch(url: str) -> str:
        """Fetch a URL and return its text content."""
        try:
            result = subprocess.run(
                ["curl", "-s", "-L", "-A", "Mozilla/5.0", url],
                capture_output=True, timeout=15, check=False,
            )
            return result.stdout.decode(errors="replace")[:15000]
        except Exception as e:
            logger.warning("Web fetch failed: %s", e)
            return ""

    app_ref = AppReference(
        name=name,
        package_name=package,
        app_store_url=app_store_url,
        description=description,
        baseline=baseline,
        differentiation=differentiation,
    )

    layer = ContextGatheringLayer(
        ai=ai,
        web_search=web_search,
        web_fetch=web_fetch,
    )

    console.print(f"[bold]Gathering context for:[/bold] {name or package or 'app'}")
    prior_context = await layer.gather(app_ref)

    json_output = prior_context.model_dump_json(indent=2)

    if output:
        Path(output).write_text(json_output)
        console.print(f"[green]Context saved to:[/green] {output}")
    else:
        console.print()
        console.print(Panel(
            f"[bold]{prior_context.app_name}[/bold]\n{prior_context.description}",
            title=f"App Context ({prior_context.category or 'unknown category'})",
        ))

        if prior_context.expected_capabilities:
            console.print()
            cap_table = Table(title="Expected Capabilities")
            cap_table.add_column("Capability", style="cyan")
            cap_table.add_column("Importance", justify="center")
            cap_table.add_column("Frequency", justify="center")
            cap_table.add_column("Description")
            for cap in prior_context.expected_capabilities:
                cap_table.add_row(
                    cap.name,
                    f"{cap.estimated_importance:.0%}",
                    f"{cap.estimated_frequency:.0%}",
                    cap.description[:60],
                )
            console.print(cap_table)

        if prior_context.expected_entities:
            console.print()
            ent_table = Table(title="Expected Entities")
            ent_table.add_column("Entity", style="cyan")
            ent_table.add_column("Description")
            for ent in prior_context.expected_entities:
                ent_table.add_row(ent.name, ent.description[:60])
            console.print(ent_table)

        console.print()
        console.print("[dim]Full JSON output:[/dim]")
        console.print(json_output)


@main.command()
@click.argument("observations", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--prior-context", type=click.Path(exists=True), help="Prior context JSON from Layer 0")
@click.option("--current-model", type=click.Path(exists=True), help="Existing model to update")
@click.option("--app-name", type=str, help="App name (used if no current model)")
@click.option("--package", type=str, help="Package name (used if no current model)")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file (default: stdout)")
def model(
    observations: tuple[str, ...],
    prior_context: str | None,
    current_model: str | None,
    app_name: str | None,
    package: str | None,
    output: str | None,
) -> None:
    """Build or update an ApplicationModel from screen observations.

    OBSERVATIONS are paths to ScreenObservation JSON files (from `pathfinder perceive`).

    Examples:

        pathfinder model obs1.json obs2.json obs3.json -o app_model.json

        pathfinder model obs/*.json --prior-context context.json -o app_model.json

        pathfinder model new_obs.json --current-model app_model.json -o app_model_v2.json
    """
    asyncio.run(_model_async(observations, prior_context, current_model, app_name, package, output))


async def _model_async(
    observation_paths: tuple[str, ...],
    prior_context_path: str | None,
    current_model_path: str | None,
    app_name: str | None,
    package: str | None,
    output: str | None,
) -> None:
    """Async implementation of the model command."""
    from pathfinder.contracts.app_reference import AppReference
    from pathfinder.layers.perception import PerceptionLayer
    from pathfinder.layers.context_gathering import ContextGatheringLayer
    from pathfinder.layers.world_modeling import WorldModelingLayer

    config = get_ai_config()
    ai = get_ai(config)
    layer = WorldModelingLayer(ai=ai)

    # Load observations
    obs_list = []
    for obs_path in observation_paths:
        try:
            obs = PerceptionLayer.load_observation(obs_path)
            obs_list.append(obs)
            console.print(f"  Loaded observation: {obs.screen_purpose} ({obs.observation_id})")
        except Exception as e:
            console.print(f"[yellow]Warning:[/yellow] Failed to load {obs_path}: {e}")

    if not obs_list:
        console.print("[red]Error:[/red] No valid observations loaded")
        sys.exit(1)

    # Load prior context if provided
    prior_ctx = None
    if prior_context_path:
        prior_ctx = ContextGatheringLayer.load_context(prior_context_path)
        console.print(f"  Loaded prior context: {prior_ctx.app_name} ({prior_ctx.category})")

    # Load or create model
    if current_model_path:
        app_model = WorldModelingLayer.load_model(current_model_path)
        console.print(f"  Loaded existing model v{app_model.model_version}")
    else:
        app_ref = AppReference(
            name=app_name or (prior_ctx.app_name if prior_ctx else None),
            package_name=package,
        )
        app_model = layer.create_empty_model(app_ref, prior_ctx)

    # Update model
    console.print(f"\n[bold]Building model from {len(obs_list)} observations...[/bold]")
    app_model = await layer.update(app_model, obs_list, prior_ctx)

    # Output
    json_output = app_model.model_dump_json(indent=2)

    if output:
        Path(output).write_text(json_output)
        console.print(f"\n[green]Model saved to:[/green] {output}")
    else:
        console.print()

    # Always show summary
    console.print(Panel(
        f"[bold]{app_model.purpose or 'Unknown app'}[/bold]",
        title=f"Application Model v{app_model.model_version} — {app_model.domain or 'unknown domain'}",
    ))

    summary_table = Table(title="Model Summary")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value")
    summary_table.add_row("Screens discovered", str(len(app_model.screens)))
    summary_table.add_row("Transitions", str(len(app_model.transitions)))
    summary_table.add_row("Capabilities", str(len(app_model.capabilities)))
    summary_table.add_row("Entities", str(len(app_model.entities)))
    summary_table.add_row("Frontier items", str(len(app_model.frontier)))
    summary_table.add_row("Anomalies", str(len(app_model.anomalies)))
    summary_table.add_row("Coverage", f"{app_model.coverage_estimate:.0%}")
    summary_table.add_row("Confidence", f"{app_model.confidence:.0%}")
    console.print(summary_table)

    if app_model.screens:
        console.print()
        screen_table = Table(title="Discovered Screens")
        screen_table.add_column("ID", style="dim")
        screen_table.add_column("Name", style="cyan")
        screen_table.add_column("Type")
        screen_table.add_column("Capabilities")
        for s in app_model.screens:
            screen_table.add_row(
                s.screen_id,
                s.name,
                s.screen_type.value,
                ", ".join(s.participates_in[:3]) or "-",
            )
        console.print(screen_table)

    if app_model.frontier:
        console.print()
        frontier_table = Table(title="Exploration Frontier")
        frontier_table.add_column("Priority", justify="center")
        frontier_table.add_column("What to explore")
        frontier_table.add_column("Strategy")
        for f in sorted(app_model.frontier, key=lambda x: x.priority, reverse=True)[:10]:
            frontier_table.add_row(
                f"{f.priority:.0%}",
                f.description[:60],
                f.search_strategy[:40],
            )
        console.print(frontier_table)

    if not output:
        console.print()
        console.print("[dim]Full JSON output:[/dim]")
        console.print(json_output)


@main.command("generate-flows")
@click.argument("model_path", type=click.Path(exists=True))
@click.argument("summary_path", type=click.Path(exists=True))
@click.option("--prior-context", type=click.Path(exists=True), help="Prior context JSON from Layer 0")
@click.option("--output", "-o", type=click.Path(), help="Output JSON file (default: stdout summary + auto-save)")
def generate_flows(
    model_path: str,
    summary_path: str,
    prior_context: str | None,
    output: str | None,
) -> None:
    """Generate user flows from a saved model and exploration summary.

    MODEL_PATH is the path to a final_model.json from a previous exploration run.
    SUMMARY_PATH is the path to the corresponding exploration_summary.json.

    This is Layer 3 in pipeline mode — it takes previously saved Layer 2 output
    and produces structured Flow objects.

    Examples:

        pathfinder generate-flows exploration/run-id/final_model.json exploration/run-id/exploration_summary.json

        pathfinder generate-flows model.json summary.json --prior-context context.json -o flows.json
    """
    asyncio.run(_generate_flows_async(model_path, summary_path, prior_context, output))


async def _generate_flows_async(
    model_path: str,
    summary_path: str,
    prior_context_path: str | None,
    output: str | None,
) -> None:
    """Async implementation of generate-flows."""
    from pathfinder.layers.flow_generation import FlowGenerationLayer

    config = get_ai_config()
    ai = get_ai(config)
    layer = FlowGenerationLayer(ai=ai)

    console.print(f"[bold]Generating flows from:[/bold]")
    console.print(f"  Model: {model_path}")
    console.print(f"  Trace: {summary_path}")
    console.print()

    flow_set = await layer.generate_from_files(
        model_path=model_path,
        summary_path=summary_path,
        prior_context_path=prior_context_path,
    )

    # Determine output path
    if output:
        out_path = output
    else:
        # Auto-save next to the model file
        out_path = str(Path(model_path).parent / "flows.json")

    FlowGenerationLayer.save_flows(flow_set, out_path)
    console.print(f"[green]Flows saved to:[/green] {out_path}")

    # Display summary
    _display_flows(flow_set)


def _display_flows(flow_set) -> None:
    """Display a FlowSet summary in the terminal."""
    from pathfinder.contracts.flow import FlowSet as FS

    if not flow_set.flows:
        console.print("[yellow]No flows generated.[/yellow]")
        return

    console.print(Panel(
        f"[bold]{len(flow_set.flows)} flows discovered[/bold]",
        title="Flow Generation Results",
    ))

    flow_table = Table(title="Discovered Flows")
    flow_table.add_column("#", style="dim")
    flow_table.add_column("Goal")
    flow_table.add_column("Category", style="cyan")
    flow_table.add_column("Importance", justify="center")
    flow_table.add_column("Frequency", justify="center")
    flow_table.add_column("Status")
    flow_table.add_column("Steps", justify="center")

    for flow in flow_set.flows:
        cat = flow.category.value if hasattr(flow.category, 'value') else flow.category
        status_style = {
            "validated": "[green]validated[/green]",
            "hypothetical": "[yellow]hypothetical[/yellow]",
            "failed": "[red]failed[/red]",
        }.get(flow.validation_status, flow.validation_status)

        n_steps = len(flow.semantic_steps)
        if flow.concrete_steps:
            n_steps = f"{len(flow.semantic_steps)}s/{len(flow.concrete_steps)}c"

        flow_table.add_row(
            flow.flow_id,
            flow.goal[:50],
            cat,
            f"{flow.importance:.0%}",
            f"{flow.estimated_frequency:.0%}",
            status_style,
            str(n_steps),
        )

    console.print(flow_table)

    # Show details for the top flows
    top_flows = flow_set.flows[:3]
    for flow in top_flows:
        console.print()
        cat = flow.category.value if hasattr(flow.category, 'value') else flow.category
        console.print(f"  [bold]{flow.flow_id}:[/bold] {flow.goal} [{cat}]")
        if flow.preconditions:
            console.print(f"    Pre: {', '.join(flow.preconditions[:2])}")
        if flow.postconditions:
            console.print(f"    Post: {', '.join(flow.postconditions[:2])}")
        for step in flow.semantic_steps:
            console.print(f"    {step.step_number}. {step.intent}")


@main.command("visualise")
@click.argument("run_dir", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Output HTML file (default: <run_dir>/flows_visual.html)")
@click.option("--open", "open_browser", is_flag=True, help="Open the HTML in the default browser after generating")
def visualise(run_dir: str, output: str | None, open_browser: bool) -> None:
    """Generate an interactive flow visualisation from a run directory.

    RUN_DIR is the path to a completed exploration run directory
    (containing flows.json, exploration_summary.json, screenshots, etc.)

    Produces a self-contained HTML file with embedded screenshots that
    can be opened in any browser.

    Examples:

        pathfinder visualise exploration/20260407-143052-a7f3/

        pathfinder visualise exploration/20260407-143052-a7f3/ --open

        pathfinder visualise exploration/20260407-143052-a7f3/ -o report.html
    """
    from pathfinder.visualise import generate_visualisation

    console.print(f"[bold]Generating visualisation for:[/bold] {run_dir}")

    try:
        out_path = generate_visualisation(run_dir, output)
        console.print(f"[green]Visualisation saved to:[/green] {out_path}")

        if open_browser:
            import webbrowser
            webbrowser.open(f"file://{Path(out_path).resolve()}")
            console.print("[dim]Opened in browser[/dim]")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@main.command("explore-web")
@click.argument("url")
@click.option("--name", type=str, help="App/site name")
@click.option("--description", type=str, help="What this app does")
@click.option("--max-actions", type=int, default=20, help="Exploration budget (default: 20)")
@click.option("--output-dir", "-o", type=click.Path(), default="./exploration", help="Output directory")
@click.option("--headless", is_flag=True, help="Run browser headless (no visible window)")
@click.option("--browser", type=click.Choice(["chromium", "firefox", "webkit"]), default="chromium")
@click.option("--prior-context", type=click.Path(exists=True), help="Prior context JSON from Layer 0")
@click.option("--viewport", type=str, default="1280x800", help="Viewport WIDTHxHEIGHT (default: 1280x800)")
@click.option(
    "--inputs", type=click.Path(exists=True),
    help="JSON file with input specs (from a previous run's inputs_required.json, edited with strategies/values)",
)
@click.option(
    "--interactive", is_flag=True,
    help="Enable interactive mode: pause and ask for input when 'ask' strategy is encountered",
)
@click.option(
    "--deep-trace", is_flag=True,
    help="Save screenshot + full page source for each step into a deeptrace/ subdirectory",
)
@click.option(
    "--run-prefix", type=str, default="pathfinder-run-",
    help="Prefix for the run directory name (default: 'pathfinder-run-')",
)
def explore_web(
    url: str,
    name: str | None,
    description: str | None,
    max_actions: int,
    output_dir: str,
    headless: bool,
    browser: str,
    prior_context: str | None,
    viewport: str,
    inputs: str | None,
    interactive: bool,
    deep_trace: bool,
    run_prefix: str,
) -> None:
    """Explore a web application autonomously and build an ApplicationModel.

    URL is the starting page of the web application to explore.

    This runs the full agent loop: perceive → update model → plan → act → repeat.
    Each run gets a unique run ID and its own subdirectory under the output dir.

    When the system encounters input fields it can't fill (login forms, search
    boxes, etc.), it records them in inputs_required.json. You can review that
    file, add strategies and values, and pass it back with --inputs on the next
    run.

    Examples:

        pathfinder explore-web https://news.ycombinator.com --name "Hacker News"

        pathfinder explore-web https://example.com --max-actions 30 -o ./output

        pathfinder explore-web https://app.example.com --inputs inputs.json

        pathfinder explore-web https://app.example.com --inputs inputs.json --interactive
    """
    # Reconstruct the invocation command for logging
    parts = ["pathfinder", "explore-web", url]
    if name:
        parts.extend(["--name", repr(name)])
    if description:
        parts.extend(["--description", repr(description)])
    if max_actions != 20:
        parts.extend(["--max-actions", str(max_actions)])
    if output_dir != "./exploration":
        parts.extend(["-o", output_dir])
    if headless:
        parts.append("--headless")
    if browser != "chromium":
        parts.extend(["--browser", browser])
    if prior_context:
        parts.extend(["--prior-context", prior_context])
    if viewport != "1280x800":
        parts.extend(["--viewport", viewport])
    if inputs:
        parts.extend(["--inputs", inputs])
    if interactive:
        parts.append("--interactive")
    if deep_trace:
        parts.append("--deep-trace")
    if run_prefix != "pathfinder-run-":
        parts.extend(["--run-prefix", repr(run_prefix)])
    invocation_command = " ".join(parts)

    asyncio.run(_explore_web_async(
        url, name, description, max_actions, output_dir,
        headless, browser, prior_context, viewport, invocation_command,
        inputs, interactive, deep_trace, run_prefix,
    ))


async def _explore_web_async(
    url: str,
    name: str | None,
    description: str | None,
    max_actions: int,
    output_dir: str,
    headless: bool,
    browser: str,
    prior_context_path: str | None,
    viewport: str,
    invocation_command: str = "",
    inputs_path: str | None = None,
    interactive: bool = False,
    deep_trace: bool = False,
    run_prefix: str = "pathfinder-run-",
) -> None:
    """Async implementation of explore-web."""
    from pathfinder.contracts.app_reference import AppReference
    from pathfinder.contracts.inputs import InputRegistry
    from pathfinder.device.web.adapter import WebDeviceAdapter
    from pathfinder.layers.context_gathering import ContextGatheringLayer
    from pathfinder.orchestrator.agent_loop import AgentLoop, ExplorationConfig, generate_run_id

    config = get_ai_config()
    ai = get_ai(config)

    # Generate the run ID up front so we can show it early
    # Normalise: strip any trailing hyphen from prefix, then always join with one
    run_id = run_prefix.rstrip("-") + "-" + generate_run_id()

    # Load input specs if provided
    input_specs = None
    if inputs_path:
        input_specs = InputRegistry.load_specs(inputs_path)
        console.print(f"  Loaded {len(input_specs)} input specs from {inputs_path}")

    # Parse viewport
    try:
        vw, vh = viewport.split("x")
        viewport_width, viewport_height = int(vw), int(vh)
    except ValueError:
        console.print(f"[yellow]Warning:[/yellow] Invalid viewport '{viewport}', using 1280x800")
        viewport_width, viewport_height = 1280, 800

    # Build app reference
    app_ref = AppReference(
        name=name or url,
        web_url=url,
        description=description,
    )

    # Load prior context if provided
    prior_ctx = None
    if prior_context_path:
        prior_ctx = ContextGatheringLayer.load_context(prior_context_path)
        console.print(f"  Loaded prior context: {prior_ctx.app_name} ({prior_ctx.category})")

    # Create the web adapter
    adapter = WebDeviceAdapter(
        headless=headless,
        browser_type=browser,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )

    exploration_config = ExplorationConfig(
        max_actions=max_actions,
        output_dir=output_dir,
        input_specs=input_specs,
        interactive=interactive,
        deep_trace=deep_trace,
    )

    run_dir = os.path.join(output_dir, run_id)

    console.print(f"[bold]Exploring:[/bold] {url}")
    console.print(f"  Run ID: {run_id}")
    console.print(f"  Browser: {browser} ({'headless' if headless else 'headed'})")
    console.print(f"  Viewport: {viewport_width}x{viewport_height}")
    console.print(f"  Budget: {max_actions} actions")
    if deep_trace:
        console.print(f"  Deep trace: ON (screenshots + page source in deeptrace/)")
    console.print(f"  Output: {run_dir}")
    console.print()

    try:
        await adapter.start()

        loop = AgentLoop(ai=ai, device=adapter, config=exploration_config)
        result = await loop.explore(
            app_ref=app_ref,
            prior_context=prior_ctx,
            start_url=url,
            run_id=run_id,
            invocation_command=invocation_command,
        )

        # Show results
        console.print()
        console.print(Panel(
            f"[bold]{result.model.purpose or app_ref.name}[/bold]",
            title=f"Exploration Complete — {result.total_actions} actions in {result.duration_seconds:.1f}s",
            subtitle=f"Run: {run_id}",
        ))

        summary_table = Table(title="Exploration Results")
        summary_table.add_column("Metric", style="cyan")
        summary_table.add_column("Value")
        summary_table.add_row("Run ID", result.run_id)
        summary_table.add_row("Stop reason", result.stop_reason)
        summary_table.add_row("Screens discovered", str(len(result.model.screens)))
        summary_table.add_row("Transitions", str(len(result.model.transitions)))
        summary_table.add_row("Capabilities", str(len(result.model.capabilities)))
        summary_table.add_row("Coverage", f"{result.model.coverage_estimate:.0%}")
        summary_table.add_row("Confidence", f"{result.model.confidence:.0%}")
        summary_table.add_row("Model version", str(result.model.model_version))
        console.print(summary_table)

        if result.model.screens:
            console.print()
            screen_table = Table(title="Discovered Screens")
            screen_table.add_column("Name", style="cyan")
            screen_table.add_column("Type")
            screen_table.add_column("Capabilities")
            for s in result.model.screens:
                screen_table.add_row(
                    s.name,
                    s.screen_type.value,
                    ", ".join(s.participates_in[:3]) or "-",
                )
            console.print(screen_table)

        if result.steps:
            console.print()
            step_table = Table(title="Exploration Steps")
            step_table.add_column("#", style="dim")
            step_table.add_column("Screen")
            step_table.add_column("Action")
            step_table.add_column("Goal")
            for s in result.steps:
                step_table.add_row(
                    str(s.step_number),
                    s.observation.screen_purpose[:40],
                    s.action_executed[:40],
                    s.plan.exploration_goal[:40] if s.plan.exploration_goal else "-",
                )
            console.print(step_table)

        if result.flow_set and result.flow_set.flows:
            console.print()
            _display_flows(result.flow_set)

        if result.input_requests:
            console.print()
            input_table = Table(title="Inputs Required (not supplied)")
            input_table.add_column("Field", style="cyan")
            input_table.add_column("Category")
            input_table.add_column("Screen")
            input_table.add_column("Suggested Strategy")
            input_table.add_column("Notes")
            for ir in result.input_requests:
                input_table.add_row(
                    ir.field,
                    ir.category,
                    ir.screen_type,
                    ir.suggested_strategy,
                    ir.notes[:40] if ir.notes else "-",
                )
            console.print(input_table)
            console.print(
                f"\n[yellow]Tip:[/yellow] Review {result.run_dir}/inputs_required.json, "
                f"add strategies and values, then re-run with --inputs to explore deeper."
            )

        # Generate visual report if we have flows
        vis_path = None
        if result.flow_set and result.flow_set.flows:
            try:
                from pathfinder.visualise import generate_visualisation
                vis_path = generate_visualisation(result.run_dir)
                console.print(f"\n[bold green]Interactive visualisation:[/bold green] {vis_path}")
                console.print("[dim]  Open in a browser to explore flows with screenshots and playback[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning:[/yellow] Could not generate visualisation: {e}")

        console.print(f"\n[green]Full results saved to:[/green] {result.run_dir}/")
        console.print(f"  Model: {result.run_dir}/final_model.json")
        console.print(f"  Summary: {result.run_dir}/exploration_summary.json")
        if result.flow_set and result.flow_set.flows:
            console.print(f"  Flows: {result.run_dir}/flows.json")
        if vis_path:
            console.print(f"  Visual: {vis_path}")
        if result.input_requests:
            console.print(f"  Inputs needed: {result.run_dir}/inputs_required.json")
        if deep_trace:
            console.print(f"  Deep trace: {result.run_dir}/deeptrace/")
        console.print(f"  Log: {result.run_dir}/run-log")
        console.print(f"  Invocation: {result.run_dir}/invocation")

    finally:
        await adapter.stop()


@main.command("serve")
@click.option("--host", type=str, default="localhost", help="Host to bind the WebSocket server to")
@click.option("--port", type=int, default=9720, help="Port for the WebSocket server")
def serve(host: str, port: int) -> None:
    """Start the Pathfinder WebSocket server for IDE integration.

    Launches a WebSocket server that external UIs (like Pathfinder Studio)
    connect to. The server accepts exploration commands and streams events
    back in real time.

    Examples:

        pathfinder serve

        pathfinder serve --port 9721

        pathfinder serve --host 0.0.0.0 --port 9720
    """
    from pathfinder.server import run_server

    console.print(f"[bold]Pathfinder Server[/bold]")
    console.print(f"  WebSocket: ws://{host}:{port}")
    console.print(f"  Press Ctrl+C to stop")
    console.print()
    run_server(host=host, port=port)


if __name__ == "__main__":
    main()
