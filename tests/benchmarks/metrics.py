"""Metrics computed from Pathfinder run results.

These are objective, reproducible metrics that can be tracked across
iterations to measure improvement or regression.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunMetrics:
    """All quantitative metrics extracted from a single run."""

    # Identity
    run_id: str
    benchmark_id: str

    # Coverage metrics
    screens_discovered: int
    transitions_discovered: int
    capabilities_discovered: int
    coverage_estimate: float  # structural (post-fix) or LLM-reported
    entities_discovered: int

    # Efficiency metrics
    total_actions: int
    actions_per_new_screen: float   # lower is better (efficient exploration)
    unique_screen_types: int        # variety of screens discovered

    # Quality metrics
    avg_elements_per_screen: float  # higher = richer perception
    frontier_size_final: int        # smaller = more complete exploration
    anomalies_detected: int

    # Reliability metrics
    stuck_events: int               # number of stuck/cycle events in log
    blank_screenshot_events: int    # number of blank screenshot warnings
    parse_errors: int               # number of AI response parse failures

    # Flow metrics
    flows_generated: int
    observed_flows: int
    hypothetical_flows: int

    # Timing (from log or summary if available)
    duration_seconds: float

    @classmethod
    def from_run_dir(cls, run_dir: str, benchmark_id: str = "unknown") -> "RunMetrics":
        """Extract metrics from a completed run directory."""
        run_path = Path(run_dir)

        summary = {}
        summary_path = run_path / "exploration_summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                summary = json.load(f)

        model = {}
        model_path = run_path / "final_model.json"
        if model_path.exists():
            with open(model_path) as f:
                model = json.load(f)

        # Parse log for reliability events
        stuck_events = 0
        blank_events = 0
        parse_errors = 0
        log_path = run_path / "run-log"
        if log_path.exists():
            log_text = log_path.read_text()
            stuck_events = log_text.count("STUCK:") + log_text.count("CYCLE DETECTED")
            blank_events = log_text.count("Screenshot appears blank")
            parse_errors = log_text.count("Failed to parse")

        screens = model.get("screens", [])
        caps = model.get("capabilities", [])
        transitions = model.get("transitions", [])
        frontier = model.get("frontier", [])
        anomalies = model.get("anomalies", [])
        entities = model.get("entities", [])

        # Compute elements-per-screen from observation files
        obs_files = list(run_path.glob("step_*_observation.json"))
        total_elements = 0
        for obs_file in obs_files:
            try:
                obs = json.loads(obs_file.read_text())
                total_elements += len(obs.get("elements", []))
            except Exception:
                pass
        avg_elements = total_elements / max(len(obs_files), 1)

        total_actions = summary.get("total_actions", 0)
        screens_count = summary.get("screens_discovered", len(screens))

        # Flow stats
        flows = summary.get("flows", [])
        observed_flows = sum(1 for f in flows if f.get("status") != "hypothetical")
        hypothetical_flows = sum(1 for f in flows if f.get("status") == "hypothetical")

        unique_screen_types = len(set(s.get("screen_type") for s in screens))

        return cls(
            run_id=summary.get("run_id", run_path.name),
            benchmark_id=benchmark_id,
            screens_discovered=screens_count,
            transitions_discovered=summary.get("transitions_discovered", len(transitions)),
            capabilities_discovered=summary.get("capabilities", len(caps)),
            coverage_estimate=summary.get("coverage_estimate", 0.0),
            entities_discovered=len(entities),
            total_actions=total_actions,
            actions_per_new_screen=total_actions / max(screens_count, 1),
            unique_screen_types=unique_screen_types,
            avg_elements_per_screen=avg_elements,
            frontier_size_final=len(frontier),
            anomalies_detected=len(anomalies),
            stuck_events=stuck_events,
            blank_screenshot_events=blank_events,
            parse_errors=parse_errors,
            flows_generated=len(flows),
            observed_flows=observed_flows,
            hypothetical_flows=hypothetical_flows,
            duration_seconds=summary.get("duration_seconds", 0.0),
        )

    def summary_table(self) -> str:
        """Return a formatted summary table."""
        lines = [
            f"Run: {self.run_id} ({self.benchmark_id})",
            f"{'─'*50}",
            f"Coverage:     {self.screens_discovered} screens, "
            f"{self.transitions_discovered} transitions, {self.coverage_estimate:.0%}",
            f"Capabilities: {self.capabilities_discovered} capabilities, "
            f"{self.entities_discovered} entities",
            f"Efficiency:   {self.total_actions} actions, "
            f"{self.actions_per_new_screen:.1f} actions/screen",
            f"Flows:        {self.flows_generated} total "
            f"({self.observed_flows} observed, {self.hypothetical_flows} hypothetical)",
            f"Reliability:  {self.stuck_events} stuck events, "
            f"{self.blank_screenshot_events} blank screenshots, "
            f"{self.parse_errors} parse errors",
            f"Duration:     {self.duration_seconds:.0f}s",
        ]
        return "\n".join(lines)
