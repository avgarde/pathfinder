"""Pathfinder Benchmark Suite.

Evaluates exploration quality against well-known, stable websites.
Each benchmark defines:
  - A target URL and app name
  - Expected screens (should be discovered)
  - Expected capabilities (should be identified)
  - Expected flows (should be generated)
  - Quality thresholds (minimum acceptable scores)

Usage:
    python -m tests.benchmarks.run_benchmarks --url https://news.ycombinator.com --max-actions 30
    python -m tests.benchmarks.evaluate --run-dir ./exploration/20260410-hn-run/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkExpectation:
    """What a good exploration of this app should produce."""

    # Screens: list of (screen_type, description fragment) pairs
    # At least ONE screen matching each entry should appear in the model
    expected_screens: list[tuple[str, str]] = field(default_factory=list)

    # Capabilities: keywords that should appear in discovered capabilities
    expected_capability_keywords: list[str] = field(default_factory=list)

    # Anti-patterns: things that should NOT happen
    # e.g., loops detected, zero transitions, single screen
    forbidden_patterns: list[str] = field(default_factory=list)

    # Minimum thresholds
    min_screens_discovered: int = 3
    min_capabilities: int = 1
    min_transitions: int = 2
    min_coverage_estimate: float = 0.15  # 15% — very conservative

    # Within max_actions budget
    max_actions: int = 30


@dataclass
class Benchmark:
    """A single benchmark configuration."""
    id: str
    name: str
    url: str
    description: str
    expectations: BenchmarkExpectation
    tags: list[str] = field(default_factory=list)  # e.g., ["simple", "auth-free", "content"]


# ---------------------------------------------------------------------------
# The benchmark suite
# ---------------------------------------------------------------------------

BENCHMARKS: dict[str, Benchmark] = {

    "hacker-news": Benchmark(
        id="hacker-news",
        name="Hacker News",
        url="https://news.ycombinator.com",
        description="Simple content/link aggregator. No auth required for browsing. "
                    "Ideal for baseline testing — clear screen types, stable structure.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("list", "front page"),
                ("list", "comments"),   # comment thread
                ("list", "new"),        # /newest or /new
            ],
            expected_capability_keywords=["read", "comment", "vote", "submit"],
            min_screens_discovered=3,
            min_capabilities=2,
            min_transitions=3,
            min_coverage_estimate=0.20,
            max_actions=25,
        ),
        tags=["simple", "auth-free", "content", "baseline"],
    ),

    "wikipedia": Benchmark(
        id="wikipedia",
        name="Wikipedia",
        url="https://en.wikipedia.org/wiki/Main_Page",
        description="Reference/encyclopedia site. No auth. Rich navigation structure "
                    "with search, categories, article depth. Tests exploration of "
                    "deeply nested content.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("home", "main page"),
                ("detail", "article"),
                ("search", "search"),
            ],
            expected_capability_keywords=["search", "read", "navigate", "edit"],
            min_screens_discovered=3,
            min_capabilities=2,
            min_transitions=4,
            min_coverage_estimate=0.15,
            max_actions=30,
        ),
        tags=["content", "auth-free", "search", "deep-navigation"],
    ),

    "github-explore": Benchmark(
        id="github-explore",
        name="GitHub Explore (public)",
        url="https://github.com/explore",
        description="GitHub's public explore page. No auth required. Tests handling of "
                    "complex UI with tabs, filters, and card-based content discovery. "
                    "Auth wall appears for user-specific features.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("list", "explore"),
                ("list", "trending"),
                ("detail", "repository"),
            ],
            expected_capability_keywords=["browse", "trending", "search", "repository"],
            forbidden_patterns=["no_screens_after_10_steps", "stuck_immediately"],
            min_screens_discovered=3,
            min_capabilities=2,
            min_transitions=3,
            min_coverage_estimate=0.15,
            max_actions=30,
        ),
        tags=["developer", "auth-partial", "complex-ui"],
    ),

    "cricinfo": Benchmark(
        id="cricinfo",
        name="ESPNcricinfo",
        url="https://www.espncricinfo.com",
        description="Sports news and statistics site. Complex content site with scores, "
                    "articles, stats, and match coverage. Tests handling of heavy JS SPAs "
                    "and content-rich pages.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("home", "home"),
                ("list", "series"),
                ("list", "match"),
            ],
            expected_capability_keywords=["scores", "news", "stats", "match"],
            min_screens_discovered=4,
            min_capabilities=3,
            min_transitions=4,
            min_coverage_estimate=0.15,
            max_actions=40,
        ),
        tags=["sports", "spa", "content-heavy", "real-world"],
    ),

    "books-to-scrape": Benchmark(
        id="books-to-scrape",
        name="Books to Scrape (test e-commerce)",
        url="https://books.toscrape.com",
        description="Purpose-built scraping/testing demo e-commerce site. Stable, "
                    "fast, no JS complexity. Perfect ground truth for e-commerce "
                    "flow discovery — product listing, detail, category, cart.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("home", "home"),
                ("list", "catalogue"),
                ("detail", "book"),
                ("list", "category"),
            ],
            expected_capability_keywords=["browse", "product", "category", "add to basket"],
            min_screens_discovered=4,
            min_capabilities=3,
            min_transitions=5,
            min_coverage_estimate=0.25,
            max_actions=30,
        ),
        tags=["e-commerce", "auth-free", "stable", "ground-truth"],
    ),

    "tosdr": Benchmark(
        id="tosdr",
        name="Terms of Service; Didn't Read",
        url="https://tosdr.org",
        description="Simple directory/rating site. Good for testing classification "
                    "and search flows on a moderate-complexity site.",
        expectations=BenchmarkExpectation(
            expected_screens=[
                ("home", "home"),
                ("search", "search"),
                ("detail", "service"),
            ],
            expected_capability_keywords=["search", "rate", "browse"],
            min_screens_discovered=3,
            min_capabilities=2,
            min_transitions=3,
            min_coverage_estimate=0.20,
            max_actions=25,
        ),
        tags=["directory", "search", "simple", "auth-free"],
    ),
}


def get_benchmark(benchmark_id: str) -> Benchmark:
    """Get a benchmark by ID."""
    if benchmark_id not in BENCHMARKS:
        available = ", ".join(BENCHMARKS.keys())
        raise ValueError(f"Unknown benchmark '{benchmark_id}'. Available: {available}")
    return BENCHMARKS[benchmark_id]


def list_benchmarks() -> list[Benchmark]:
    """Return all benchmarks."""
    return list(BENCHMARKS.values())
