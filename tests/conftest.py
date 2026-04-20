"""Shared pytest fixtures + markers."""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "network: integration tests that hit the real transfer.it/bt7 "
        "servers.  Skipped unless TRANSFERIT_ONLINE_TESTS=1.",
    )


def pytest_collection_modifyitems(config, items):
    """Skip `@pytest.mark.network` tests unless explicitly opted-in."""
    if os.environ.get("TRANSFERIT_ONLINE_TESTS") == "1":
        return
    skip = pytest.mark.skip(
        reason="set TRANSFERIT_ONLINE_TESTS=1 to run network-backed tests",
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)
