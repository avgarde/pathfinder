"""CLI script to run Pathfinder on a benchmark and evaluate the result.

Usage:
    python -m tests.benchmarks.run_benchmarks list
    python -m tests.benchmarks.run_benchmarks run hacker-news
    python -m tests.benchmarks.run_benchmarks run books-to-scrape --max-actions 40
    python -m tests.benchmarks.run_benchmarks evaluate --run-dir ./exploration/xyz/ --benchmark hacker-news
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from tests.benchmarks.benchmark_suite import BENCHMARKS, get_benchmark, list_benchmarks
from tests.benchmarks.evaluator import evaluate_run


def cmd_list(_args: argparse.Namespace) -> None:
    """List all available benchmarks."""
    print(f"\nAvailable benchmarks ({len(BENCHMARKS)} total):\n")
    for b in list_benchmarks():
        tags = ", ".join(b.tags)
        print(f"  {b.id:<25} {b.name}")
        print(f"  {'':25} URL: {b.url}")
        print(f"  {'':25} Tags: [{tags}]")
        print(f"  {'':25} Budget: {b.expectations.max_actions} actions")
        print()


def cmd_run(args: argparse.Namespace) -> None:
    """Run a benchmark and evaluate the result."""
    benchmark = get_benchmark(args.benchmark)
    max_actions = args.max_actions or benchmark.expectations.max_actions

    print(f"\nRunning benchmark: {benchmark.name}")
    print(f"URL: {benchmark.url}")
    print(f"Max actions: {max_actions}")
    print()

    # Build the pathfinder command
    output_dir = args.output_dir or "./exploration"
    cmd = [
        sys.executable, "-m", "pathfinder",
        "explore-web", benchmark.url,
        "--name", benchmark.name,
        "--max-actions", str(max_actions),
        "--output-dir", output_dir,
        "--run-prefix", f"{benchmark.id}-",
    ]

    if args.headless is not None:
        cmd.extend(["--headless", str(args.headless)])

    print(f"Command: {' '.join(cmd)}\n")

    try:
        result = subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: pathfinder command failed with exit code {e.returncode}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)

    # Find the run directory (most recent one matching benchmark prefix)
    output_path = Path(output_dir)
    matching_dirs = sorted(
        [d for d in output_path.iterdir() if d.is_dir() and d.name.startswith(benchmark.id)],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )

    if not matching_dirs:
        print(f"ERROR: Could not find run directory in {output_dir}")
        sys.exit(1)

    run_dir = matching_dirs[0]
    print(f"\nEvaluating run: {run_dir}")

    eval_result = evaluate_run(str(run_dir), benchmark)
    eval_result.print_report()

    sys.exit(0 if eval_result.passed else 1)


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Evaluate an existing run against a benchmark."""
    benchmark = get_benchmark(args.benchmark)
    eval_result = evaluate_run(args.run_dir, benchmark)
    eval_result.print_report()
    sys.exit(0 if eval_result.passed else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pathfinder benchmark runner and evaluator"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # list command
    list_parser = subparsers.add_parser("list", help="List available benchmarks")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a benchmark")
    run_parser.add_argument("benchmark", help="Benchmark ID (use 'list' to see options)")
    run_parser.add_argument("--max-actions", type=int, help="Override action budget")
    run_parser.add_argument("--output-dir", help="Output directory for exploration")
    run_parser.add_argument("--headless", type=lambda x: x.lower() == "true",
                           default=None, help="Run headless (true/false)")

    # evaluate command
    eval_parser = subparsers.add_parser("evaluate", help="Evaluate an existing run")
    eval_parser.add_argument("--run-dir", required=True, help="Path to run directory")
    eval_parser.add_argument("--benchmark", required=True, help="Benchmark ID to evaluate against")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "evaluate":
        cmd_evaluate(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
