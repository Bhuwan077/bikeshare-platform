"""
Tests for src/serving/api.py — uses FastAPI's TestClient, which runs
the app in-process rather than needing a real running server.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.serving import api as api_module
from src.serving.api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """The module caches the loaded model in globals — reset between
    tests so each test's monkeypatching of MODEL_PATH etc. actually
    takes effect instead of reusing a previously cached model."""
    api_module._model = None
    api_module._metadata = None
    yield
    api_module._model = None
    api_module._metadata = None


def test_health_when_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "MODEL_PATH", tmp_path / "does_not_exist.txt")
    monkeypatch.setattr(api_module, "METADATA_PATH", tmp_path / "does_not_exist.json")
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["model_loaded"] is False
    assert body["status"] == "degraded"


def test_predict_returns_404_for_unknown_station(monkeypatch):
    """An unknown station_id should be a clean 404, not a 500 crash —
    this requires the model and DB to load successfully, so this test
    only meaningfully passes when run against a real trained model +
    warehouse (skipped gracefully otherwise)."""
    if not api_module.MODEL_PATH.exists() or not api_module.DB_PATH.exists():
        pytest.skip("requires a trained model and built warehouse — run `dbt build` "
                    "and `python -m src.ml.train` first for this test to be meaningful")
    r = client.post("/predict", json={"station_id": "definitely-not-a-real-station"})
    assert r.status_code == 404


def test_predict_never_returns_a_raw_500_for_empty_station_status(monkeypatch, tmp_path):
    """Regression test for a real bug: an empty station_status result
    (e.g. all data aged out of the freshness window) crashed with an
    unhandled ValueError ('No objects to concatenate') instead of a
    clean error response. Verified directly against the feature
    function, which is what actually had the bug."""
    from src.features.build_features import get_latest_feature_row

    empty_status = pd.DataFrame(columns=["station_id", "fetched_at", "num_bikes_available"])
    capacity = pd.DataFrame({"station_id": ["A"], "capacity": [20]})
    forecast = pd.DataFrame(columns=["issued_at", "forecast_target_time",
                                       "temperature_2m_c", "precipitation_mm"])
    result = get_latest_feature_row(empty_status, capacity, forecast, "A")
    assert result is None  # must not raise


def test_metadata_json_is_valid_after_training(tmp_path):
    """Sanity check on the metadata format the API depends on —
    catches a format drift between train.py and api.py early."""
    if not api_module.METADATA_PATH.exists():
        pytest.skip("requires `python -m src.ml.train` to have been run first")
    metadata = json.loads(api_module.METADATA_PATH.read_text())
    assert "feature_columns" in metadata
    assert "trained_at" in metadata
    assert isinstance(metadata["feature_columns"], list)
