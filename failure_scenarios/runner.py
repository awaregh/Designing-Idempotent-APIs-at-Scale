"""
Failure scenario runner.

Executes all failure scenarios against all configured API services,
collects FailureResult objects, writes JSON to results/failure_results.json,
and prints a Rich summary table.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from failure_scenarios import FailureResult
from failure_scenarios.scenarios import (
    client_retry,
    concurrent_identical,
    dedup_test_scenario,
    duplicate_webhook,
    message_redelivery,
    network_timeout,
    partial_failure,
    worker_retry,
)

SERVICES: dict[str, str] = {
    "baseline": "http://localhost:8001",
    "idempotency_key": "http://localhost:8002",
    "natural_idempotency": "http://localhost:8003",
    "db_constraint": "http://localhost:8004",
    "dedup_queue": "http://localhost:8005",
    "event_driven": "http://localhost:8006",
    "saga": "http://localhost:8007",
}

SCENARIO_MODULES = [
    client_retry,
    network_timeout,
    duplicate_webhook,
    concurrent_identical,
    partial_failure,
    worker_retry,
    message_redelivery,
]

RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


async def run_all() -> list[FailureResult]:
    """Run every scenario against every service and return all results."""
    all_results: list[FailureResult] = []

    for service_name, base_url in SERVICES.items():
        for module in SCENARIO_MODULES:
            try:
                result: FailureResult = await module.run(
                    base_url=base_url, service_name=service_name
                )
            except Exception as exc:
                result = FailureResult(
                    scenario_name=getattr(module, "SCENARIO_NAME", module.__name__),
                    service=service_name,
                    expected_outcome="no exception",
                    actual_outcome="runner exception",
                    correct=False,
                    error=str(exc),
                )
            all_results.append(result)

    return all_results


def save_results(results: list[FailureResult]) -> Path:
    """Serialise results to JSON."""
    output_path = RESULTS_DIR / "failure_results.json"
    serialisable = [
        {
            "scenario_name": r.scenario_name,
            "service": r.service,
            "expected_outcome": r.expected_outcome,
            "actual_outcome": r.actual_outcome,
            "correct": r.correct,
            "details": r.details,
            "error": r.error,
        }
        for r in results
    ]
    with open(output_path, "w") as fh:
        json.dump(
            {"run_at": datetime.utcnow().isoformat(), "results": serialisable},
            fh,
            indent=2,
        )
    return output_path


def print_table(results: list[FailureResult]) -> None:
    """Print Rich summary table."""
    console = Console()
    table = Table(title="Failure Scenario Results", show_lines=True)
    table.add_column("Scenario", style="cyan", no_wrap=True)
    table.add_column("Service", style="magenta")
    table.add_column("Expected", style="white")
    table.add_column("Actual", style="white")
    table.add_column("Pass/Fail", justify="center")

    for r in results:
        status = "[green]✓ PASS[/green]" if r.correct else "[red]✗ FAIL[/red]"
        table.add_row(
            r.scenario_name,
            r.service,
            r.expected_outcome,
            r.actual_outcome or (r.error or ""),
            status,
        )

    console.print(table)
    total = len(results)
    passed = sum(1 for r in results if r.correct)
    console.print(f"\n[bold]Total: {total}  Passed: {passed}  Failed: {total - passed}[/bold]")


async def main() -> None:
    results = await run_all()
    path = save_results(results)
    print_table(results)
    print(f"\nResults written to {path}")


if __name__ == "__main__":
    asyncio.run(main())
