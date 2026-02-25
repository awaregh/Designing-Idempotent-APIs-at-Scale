"""Shared re-exports for scenario modules used by the runner."""
from __future__ import annotations

from failure_scenarios.scenarios import (
    client_retry,
    concurrent_identical,
    duplicate_webhook,
    message_redelivery,
    network_timeout,
    partial_failure,
    worker_retry,
)

# Alias for runner import
dedup_test_scenario = message_redelivery

__all__ = [
    "client_retry",
    "concurrent_identical",
    "dedup_test_scenario",
    "duplicate_webhook",
    "message_redelivery",
    "network_timeout",
    "partial_failure",
    "worker_retry",
]
