"""
Failure scenarios package.

Provides the FailureResult dataclass used by all scenario modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FailureResult:
    """Result of a single failure scenario run against a single service."""

    scenario_name: str
    service: str
    expected_outcome: str
    actual_outcome: str
    correct: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
