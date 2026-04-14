"""Evaluate a completed Pathfinder run against benchmark expectations.

Usage:
    from tests.benchmarks.evaluator import evaluate_run
    from tests.benchmarks.benchmark_suite import get_benchmark

    benchmark = get_benchmark("hacker-news")
    result = evaluate_run("./exploration/20260410-hn-run/", benchmark)
    result.print_report()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from tests.benchmarks.benchmark_suite import Benchmark, BenchmarkExpectation


@dataclass
class EvaluationResult:
    """Result of evaluating a run against a benchmark."""
    benchmark_id: str
    run_dir: str
    passed: bool
    score: float  # 0.0 to 1.0

    # Individual check results
    checks: dict[str, bool] = field(default_factory=dict)
    check_details: dict[str, str] = field(default_factory=dict)

    # Raw stats from the run
    screens_discovered: int = 0
    capabilities_discovered: int = 0
    transitions_discovered: int = 0
    coverage_estimate: float = 0.0
    total_actions: int = 0
    stop_reason: str = ""

    def print_report(self) -> None:
        """Print a human-readable evaluation report."""
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        print(f"\n{'='*60}")
        print(f"Benchmark: {self.benchmark_id}")
        print(f"Run: {self.run_dir}")
        print(f"Result: {status} (score: {self.score:.0%})")
        print(f"{'='*60}")
        print(f"Stats:")
        print(f"  Screens:      {self.screens_discovered}")
        print(f"  Capabilities: {self.capabilities_discovered}")
        print(f"  Transitions:  {self.transitions_discovered}")
        print(f"  Coverage:     {self.coverage_estimate:.0%}")
        print(f"  Actions:      {self.total_actions}")
        print(f"  Stop reason:  {self.stop_reason}")
        print(f"\nChecks:")
        for check_name, passed in sorted(self.checks.items()):
            icon = "  ✓" if passed else "  ✗"
            detail = self.check_details.get(check_name, "")
            print(f"{icon} {check_name}: {detail}")
        print()


def evaluate_run(run_dir: str, benchmark: Benchmark) -> EvaluationResult:
    """Evaluate a completed exploration run against benchmark expectations.

    Args:
        run_dir: Path to the run directory (contains final_model.json, exploration_summary.json)
        benchmark: The benchmark to evaluate against

    Returns:
        EvaluationResult with pass/fail status and detailed check results
    """
    run_path = Path(run_dir)
    exp = benchmark.expectations

    result = EvaluationResult(
        benchmark_id=benchmark.id,
        run_dir=str(run_path),
        passed=False,
        score=0.0,
    )

    # Load exploration summary
    summary_path = run_path / "exploration_summary.json"
    model_path = run_path / "final_model.json"

    if not summary_path.exists():
        result.check_details["load_summary"] = f"Missing: {summary_path}"
        result.checks["load_summary"] = False
        return result

    with open(summary_path) as f:
        summary = json.load(f)

    result.screens_discovered = summary.get("screens_discovered", 0)
    result.capabilities_discovered = summary.get("capabilities", 0)
    result.transitions_discovered = summary.get("transitions_discovered", 0)
    result.coverage_estimate = summary.get("coverage_estimate", 0.0)
    result.total_actions = summary.get("total_actions", 0)
    result.stop_reason = summary.get("stop_reason", "unknown")
    result.checks["load_summary"] = True
    result.check_details["load_summary"] = f"Loaded {result.total_actions} steps"

    # Load model if available
    model_data = {}
    if model_path.exists():
        with open(model_path) as f:
            model_data = json.load(f)

    # --- Check: minimum screens ---
    screens_ok = result.screens_discovered >= exp.min_screens_discovered
    result.checks["min_screens"] = screens_ok
    result.check_details["min_screens"] = (
        f"{result.screens_discovered} discovered (need ≥ {exp.min_screens_discovered})"
    )

    # --- Check: minimum capabilities ---
    caps_ok = result.capabilities_discovered >= exp.min_capabilities
    result.checks["min_capabilities"] = caps_ok
    result.check_details["min_capabilities"] = (
        f"{result.capabilities_discovered} discovered (need ≥ {exp.min_capabilities})"
    )

    # --- Check: minimum transitions ---
    trans_ok = result.transitions_discovered >= exp.min_transitions
    result.checks["min_transitions"] = trans_ok
    result.check_details["min_transitions"] = (
        f"{result.transitions_discovered} discovered (need ≥ {exp.min_transitions})"
    )

    # --- Check: minimum coverage ---
    coverage_ok = result.coverage_estimate >= exp.min_coverage_estimate
    result.checks["min_coverage"] = coverage_ok
    result.check_details["min_coverage"] = (
        f"{result.coverage_estimate:.0%} (need ≥ {exp.min_coverage_estimate:.0%})"
    )

    # --- Check: expected screen types present ---
    if exp.expected_screens and model_data:
        all_screens = model_data.get("screens", [])
        screen_texts = [
            f"{s.get('screen_type', '')} {s.get('name', '')} {s.get('purpose', '')}".lower()
            for s in all_screens
        ]
        found_screens = []
        missing_screens = []
        for screen_type, desc_fragment in exp.expected_screens:
            found = any(
                screen_type.lower() in text and desc_fragment.lower() in text
                for text in screen_texts
            )
            if found:
                found_screens.append(f"{screen_type}:{desc_fragment}")
            else:
                missing_screens.append(f"{screen_type}:{desc_fragment}")

        screens_found_ratio = len(found_screens) / max(len(exp.expected_screens), 1)
        screens_check_ok = screens_found_ratio >= 0.5  # at least half of expected screens
        result.checks["expected_screen_types"] = screens_check_ok
        result.check_details["expected_screen_types"] = (
            f"Found {len(found_screens)}/{len(exp.expected_screens)}: "
            f"found={found_screens}, missing={missing_screens}"
        )
    else:
        result.checks["expected_screen_types"] = True  # skip if no model or no expectations
        result.check_details["expected_screen_types"] = "skipped (no model data)"

    # --- Check: expected capability keywords ---
    if exp.expected_capability_keywords and model_data:
        all_caps = model_data.get("capabilities", [])
        cap_texts = [
            f"{c.get('name', '')} {c.get('description', '')}".lower()
            for c in all_caps
        ]
        cap_text_combined = " ".join(cap_texts)
        found_keywords = [kw for kw in exp.expected_capability_keywords if kw.lower() in cap_text_combined]
        missing_keywords = [kw for kw in exp.expected_capability_keywords if kw.lower() not in cap_text_combined]

        keyword_ratio = len(found_keywords) / max(len(exp.expected_capability_keywords), 1)
        keywords_ok = keyword_ratio >= 0.4  # at least 40% of expected keywords
        result.checks["capability_keywords"] = keywords_ok
        result.check_details["capability_keywords"] = (
            f"Found {len(found_keywords)}/{len(exp.expected_capability_keywords)} keywords: "
            f"found={found_keywords}, missing={missing_keywords}"
        )
    else:
        result.checks["capability_keywords"] = True
        result.check_details["capability_keywords"] = "skipped"

    # --- Check: no crash/error stop reason ---
    no_crash = not result.stop_reason.startswith("Error:")
    result.checks["no_crash"] = no_crash
    result.check_details["no_crash"] = result.stop_reason

    # --- Check: reasonable actions used (didn't terminate after 1-2 steps) ---
    reasonable_actions = result.total_actions >= min(5, exp.max_actions // 3)
    result.checks["reasonable_action_count"] = reasonable_actions
    result.check_details["reasonable_action_count"] = (
        f"{result.total_actions} actions taken (need ≥ {min(5, exp.max_actions // 3)})"
    )

    # --- Compute overall score ---
    all_checks = list(result.checks.values())
    result.score = sum(all_checks) / max(len(all_checks), 1)

    # Pass threshold: 70% of checks must pass, AND the critical ones must pass
    critical_checks = ["no_crash", "min_screens", "reasonable_action_count"]
    critical_pass = all(result.checks.get(c, False) for c in critical_checks)
    result.passed = result.score >= 0.7 and critical_pass

    return result


def compare_runs(
    run_dirs: list[str],
    benchmark: Benchmark,
    labels: list[str] | None = None,
) -> None:
    """Compare multiple runs against the same benchmark (for A/B testing changes).

    Args:
        run_dirs: List of run directory paths to compare
        benchmark: Benchmark to evaluate against
        labels: Optional human-readable labels for each run
    """
    labels = labels or [f"Run {i+1}" for i in range(len(run_dirs))]
    results = [evaluate_run(d, benchmark) for d, _ in zip(run_dirs, labels)]

    print(f"\n{'='*70}")
    print(f"Benchmark Comparison: {benchmark.name}")
    print(f"{'='*70}")
    print(f"{'Check':<30} " + " ".join(f"{l:<12}" for l in labels))
    print(f"{'-'*70}")

    # Get all check names
    all_check_names = set()
    for r in results:
        all_check_names.update(r.checks.keys())

    for check_name in sorted(all_check_names):
        row = f"{check_name:<30} "
        for r in results:
            val = r.checks.get(check_name, None)
            if val is None:
                row += f"{'N/A':<12} "
            elif val:
                row += f"{'✓':<12} "
            else:
                row += f"{'✗':<12} "
        print(row)

    print(f"{'-'*70}")
    score_row = f"{'SCORE':<30} "
    status_row = f"{'STATUS':<30} "
    for r in results:
        score_row += f"{r.score:.0%}       "
        status_row += f"{'PASS' if r.passed else 'FAIL'}        "
    print(score_row)
    print(status_row)
    print()

    # Print individual reports
    for label, r in zip(labels, results):
        print(f"\n--- {label} ---")
        r.print_report()
