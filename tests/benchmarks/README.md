# Pathfinder Benchmark Suite

Evaluate and compare Pathfinder exploration quality across well-known websites.

## Available Benchmarks

| ID | Site | URL | Tags |
|----|------|-----|------|
| `hacker-news` | Hacker News | news.ycombinator.com | simple, auth-free, baseline |
| `wikipedia` | Wikipedia | en.wikipedia.org | content, search |
| `github-explore` | GitHub Explore | github.com/explore | complex-ui, auth-partial |
| `cricinfo` | ESPNcricinfo | espncricinfo.com | sports, spa, real-world |
| `books-to-scrape` | Books to Scrape | books.toscrape.com | e-commerce, ground-truth |
| `tosdr` | ToS;DR | tosdr.org | directory, simple |

## Usage

### List benchmarks
```bash
python -m tests.benchmarks.run_benchmarks list
```

### Run a benchmark
```bash
python -m tests.benchmarks.run_benchmarks run hacker-news
python -m tests.benchmarks.run_benchmarks run books-to-scrape --max-actions 40
```

### Evaluate an existing run
```bash
python -m tests.benchmarks.run_benchmarks evaluate \
  --run-dir ./exploration/hacker-news-20260410-xyz/ \
  --benchmark hacker-news
```

### A/B compare two runs (in Python)
```python
from tests.benchmarks.evaluator import compare_runs
from tests.benchmarks.benchmark_suite import get_benchmark

benchmark = get_benchmark("hacker-news")
compare_runs(
    ["./exploration/run-before/", "./exploration/run-after/"],
    benchmark,
    labels=["Before fix", "After fix"]
)
```

## What Gets Measured

Each benchmark check verifies:
- **min_screens**: Minimum distinct screens discovered
- **min_capabilities**: Minimum capabilities identified
- **min_transitions**: Minimum navigation transitions traced
- **min_coverage**: Minimum structural coverage estimate
- **expected_screen_types**: Key screen types present in model
- **capability_keywords**: Key capability keywords in model
- **no_crash**: Exploration completed without error
- **reasonable_action_count**: Used enough of the budget (not immediately stuck)

## Adding New Benchmarks

Add an entry to `BENCHMARKS` in `benchmark_suite.py`:

```python
"my-site": Benchmark(
    id="my-site",
    name="My Site",
    url="https://example.com",
    description="What this site is.",
    expectations=BenchmarkExpectation(
        expected_screens=[("home", "homepage"), ("list", "products")],
        expected_capability_keywords=["browse", "search"],
        min_screens_discovered=3,
        min_capabilities=2,
        min_transitions=3,
        min_coverage_estimate=0.20,
        max_actions=30,
    ),
    tags=["e-commerce"],
),
```
