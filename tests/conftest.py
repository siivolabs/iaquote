"""Test bootstrap that also works with the `pytest` console entry point."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from app import create_app


@pytest.fixture()
def app(tmp_path, monkeypatch):
    db = tmp_path / "test.sqlite"
    monkeypatch.setenv("BOOTSTRAP_CSV", str(ROOT / "examples" / "catalogue-demo.csv"))
    monkeypatch.setenv("PRESEED_PATH", str(ROOT / "examples" / "enrichissement-demo.json"))
    monkeypatch.setenv("START_BACKGROUND_WORKER", "0")
    application = create_app({"TESTING": True, "DATABASE_PATH": str(db), "START_BACKGROUND_WORKER": False})
    application.config["TEST_DATABASE"] = str(db)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()
