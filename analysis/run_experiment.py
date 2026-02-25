"""
Experiment orchestrator.

Runs the full idempotency strategy comparison:
  1. Health-check all docker-compose services
  2. Collect metrics for each strategy
  3. Run failure scenarios
  4. Compile summary.json
  5. Generate comparison table
  6. Generate plots
  7. Print final summary
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx
from rich.console import Console

from analysis.compare import generate_comparison_table, load_results, print_comparison
from analysis.metrics import collect_metrics
from analysis.visualize import generate_plots

SERVICES: dict[str, str] = {
    "baseline": "http://localhost:8001",
    "idempotency_key": "http://localhost:8002",
    "natural_idempotency": "http://localhost:8003",
    "db_constraint": "http://localhost:8004",
    "dedup_queue": "http://localhost:8005",
    "event_driven": "http://localhost:8006",
    "saga": "http://localhost:8007",
}

RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

console = Console()


async def check_health(name: str, url: str) -> bool:
    """Return True if the service health check passes."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/health")
            ok = r.status_code == 200
    except Exception:
        ok = False
    status = "[green]✓[/green]" if ok else "[red]✗[/red]"
    console.print(f"  {status} {name:<25} {url}")
    return ok


async def run_experiment() -> None:
    """Execute the full experiment pipeline."""
    console.rule("[bold blue]Idempotency Experiment Runner")

    # ── Step 1: Health checks ────────────────────────────────────────────────
    console.print("\n[bold]1. Health-checking services…[/bold]")
    health_tasks = [check_health(name, url) for name, url in SERVICES.items()]
    health_results = await asyncio.gather(*health_tasks)
    available = {
        name: url
        for (name, url), ok in zip(SERVICES.items(), health_results)
        if ok
    }

    if not available:
        console.print("[red]No services available. Start docker-compose first.[/red]")
        sys.exit(1)

    console.print(f"\n  {len(available)}/{len(SERVICES)} services available.\n")

    # ── Step 2: Collect metrics ──────────────────────────────────────────────
    console.print("[bold]2. Collecting metrics…[/bold]")
    metrics_list = []
    for name, url in available.items():
        console.print(f"  Collecting metrics for [cyan]{name}[/cyan]…")
        m = await collect_metrics(url, name)
        metrics_list.append(m)
        console.print(
            f"    correctness={m.get('correctness_score', 'N/A')} "
            f"p95={m.get('p95_ms', 'N/A')}ms "
            f"dup_rate={m.get('duplicate_creation_rate', 'N/A')}"
        )

    # ── Step 3: Failure scenarios ────────────────────────────────────────────
    console.print("\n[bold]3. Running failure scenarios…[/bold]")
    failure_results: list[dict] = []
    try:
        from failure_scenarios.scenarios import (
            client_retry,
            concurrent_identical,
            duplicate_webhook,
            message_redelivery,
            network_timeout,
            partial_failure,
            worker_retry,
        )

        scenario_modules = [
            client_retry, network_timeout, duplicate_webhook,
            concurrent_identical, partial_failure, worker_retry, message_redelivery,
        ]

        for name, url in available.items():
            for mod in scenario_modules:
                try:
                    result = await mod.run(base_url=url, service_name=name)
                    failure_results.append(
                        {
                            "scenario": result.scenario_name,
                            "service": result.service,
                            "correct": result.correct,
                            "actual_outcome": result.actual_outcome,
                        }
                    )
                except Exception as exc:
                    failure_results.append(
                        {
                            "scenario": getattr(mod, "SCENARIO_NAME", mod.__name__),
                            "service": name,
                            "correct": False,
                            "error": str(exc),
                        }
                    )
    except ImportError as exc:
        console.print(f"  [yellow]Skipping failure scenarios: {exc}[/yellow]")

    # ── Step 4: Save summary.json ────────────────────────────────────────────
    summary_path = RESULTS_DIR / "summary.json"
    summary = {
        "run_at": datetime.utcnow().isoformat(),
        "services_tested": list(available.keys()),
        "metrics": metrics_list,
        "failure_scenarios": failure_results,
    }
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)
    console.print(f"\n  Summary saved to [cyan]{summary_path}[/cyan]")

    # ── Step 5: Comparison table ─────────────────────────────────────────────
    console.print("\n[bold]4. Comparison table:[/bold]\n")
    results = load_results(str(summary_path))
    df = generate_comparison_table(results)
    print_comparison(df)

    # ── Step 6: Plots ─────────────────────────────────────────────────────────
    console.print("\n[bold]5. Generating plots…[/bold]")
    try:
        plots = generate_plots(str(summary_path), str(RESULTS_DIR))
        for p in plots:
            console.print(f"  [green]✓[/green] {p}")
    except Exception as exc:
        console.print(f"  [yellow]Plot generation failed: {exc}[/yellow]")

    # ── Step 7: Final summary ─────────────────────────────────────────────────
    console.rule("[bold green]Experiment Complete")
    if metrics_list:
        best = max(metrics_list, key=lambda m: m.get("correctness_score", 0))
        fastest = min(metrics_list, key=lambda m: m.get("p95_ms", float("inf")))
        console.print(f"  Best correctness : [green]{best['strategy']}[/green] ({best.get('correctness_score')})")
        console.print(f"  Fastest (P95)    : [green]{fastest['strategy']}[/green] ({fastest.get('p95_ms')} ms)")


if __name__ == "__main__":
    asyncio.run(run_experiment())
