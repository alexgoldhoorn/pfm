"""Shared fixtures: no test may reach the network."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make a real HTTP request fail loudly; tests patch ``pfm_mcp.client._get``."""

    def refuse(*args, **kwargs):
        raise AssertionError("test attempted a real network call")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
