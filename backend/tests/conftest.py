"""
Shared test fixtures. Tests go through the real HTTP routes (SPEC §17): we
spin up the actual FastAPI app with isolated XDG dirs so a test run never
touches the developer's real profiles/widgets/config.

The XDG env vars must be set BEFORE widget_dashboard is imported, because paths.py
computes its locations at import time.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Redirect all state to a throwaway dir before anything imports widget_dashboard.
_TMP = Path(tempfile.mkdtemp(prefix="wd-test-"))
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "config")
os.environ["XDG_DATA_HOME"] = str(_TMP / "data")
os.environ["XDG_STATE_HOME"] = str(_TMP / "state")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from widget_dashboard.app import app
    with TestClient(app) as c:
        yield c
