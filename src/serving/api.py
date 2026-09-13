"""
Bike availability prediction API.

Run locally:
    uvicorn src.serving.api:app --reload

Loads the model trained by src/ml/train.py (models/bikeshare_model.txt
+ models/model_metadata.json) and reuses the EXACT SAME feature
pipeline as training (src/features/build_features.py) — this is what
keeps train and serve from silently drifting apart.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import lightgbm as lgb
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.features.build_features import FEATURE_COLUMNS, get_latest_feature_row

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = REPO_ROOT / "models" / "bikeshare_model.txt"
METADATA_PATH = REPO_ROOT / "models" / "model_metadata.json"
DB_PATH = REPO_ROOT / "dbt" / "bikeshare.duckdb"
PREDICTION_LOG_PATH = REPO_ROOT / "models" / "prediction_log.parquet"

app = FastAPI(
    title="Bike Availability Prediction API",
    description="Predicts bikes_available at a station 60 minutes ahead. "
    "See docs/model-card.md for evaluation results and known limitations "
    "— in particular, this model does not yet beat a simple persistence "
    "baseline on real data (see the model card's progression table).",
)

_model: lgb.Booster | None = None
_metadata: dict | None = None


def _load_model() -> tuple[lgb.Booster, dict]:
    global _model, _metadata
    if _model is None:
        if not MODEL_PATH.exists():
            raise RuntimeError(
                f"No trained model found at {MODEL_PATH}. Run `python -m src.ml.train` first."
            )
        _model = lgb.Booster(model_file=str(MODEL_PATH))
        _metadata = json.loads(METADATA_PATH.read_text())
    return _model, _metadata


class PredictRequest(BaseModel):
    station_id: str = Field(..., description="Station's live GBFS station_id (UUID scheme)")


class PredictResponse(BaseModel):
    station_id: str
    prediction_id: str
    predicted_at: datetime
    target_time: datetime
    predicted_bikes_available: float
    confidence_interval_low: float
    confidence_interval_high: float
    model_trained_at: str
    warning: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_trained_at: str | None = None
    database_reachable: bool


def _get_connection() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Warehouse database not found at {DB_PATH}. Run `dbt build` first.",
        )
    con = duckdb.connect(str(DB_PATH), read_only=True)
    # See docs/DECISIONS.md — DuckDB's default session timezone follows
    # the host machine's OS setting, not UTC. Without this, results
    # would silently differ depending on which machine runs this API.
    con.execute("SET TimeZone='UTC'")
    return con


def _log_prediction(row: dict) -> None:
    """Appends one row to the prediction log — this is what Week 6's
    'close the loop' monitoring joins against actuals later, once 60
    minutes have elapsed, to compute live accuracy without hand
    computation."""
    PREDICTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_row = pd.DataFrame([row])
    if PREDICTION_LOG_PATH.exists():
        existing = pd.read_parquet(PREDICTION_LOG_PATH)
        combined = pd.concat([existing, new_row], ignore_index=True)
    else:
        combined = new_row
    combined.to_parquet(PREDICTION_LOG_PATH, index=False)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    model_loaded = MODEL_PATH.exists() and METADATA_PATH.exists()
    trained_at = None
    if model_loaded:
        try:
            _, metadata = _load_model()
            trained_at = metadata.get("trained_at")
        except Exception:
            model_loaded = False
    return HealthResponse(
        status="ok" if model_loaded and DB_PATH.exists() else "degraded",
        model_loaded=model_loaded,
        model_trained_at=trained_at,
        database_reachable=DB_PATH.exists(),
    )


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    model, metadata = _load_model()

    con = _get_connection()
    try:
        station_status = con.execute(
            "select station_id, fetched_at, num_bikes_available, num_docks_available "
            "from stg_station_status "
            "where fetched_at >= now() - interval '3 hours'"
        ).df()
        capacity = con.execute("select station_id, capacity from dim_station where is_current").df()
        forecast = con.execute(
            "select issued_at, forecast_target_time, temperature_2m_c, precipitation_mm "
            "from stg_weather_forecast"
        ).df()
    finally:
        con.close()

    if request.station_id not in capacity["station_id"].values:
        raise HTTPException(status_code=404, detail=f"Unknown station_id: {request.station_id}")

    latest_row = get_latest_feature_row(station_status, capacity, forecast, request.station_id)
    if latest_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No recent station_status data found for station_id: {request.station_id}",
        )

    warning = None
    missing_features = [c for c in FEATURE_COLUMNS if pd.isna(latest_row.get(c))]
    if missing_features:
        warning = (
            f"Missing/null features (model handles these natively, but treat this "
            f"prediction with extra caution): {missing_features}"
        )

    X = pd.DataFrame([latest_row[FEATURE_COLUMNS]])
    prediction = float(model.predict(X)[0])

    residual_std = metadata.get("residual_std")
    if residual_std is not None:
        ci_low = prediction - 1.96 * residual_std
        ci_high = prediction + 1.96 * residual_std
    else:
        ci_low = ci_high = prediction  # no backtest residuals available yet

    predicted_at = datetime.now(UTC)
    target_time = latest_row["fetched_at"] + pd.Timedelta(minutes=60)
    prediction_id = str(uuid.uuid4())

    _log_prediction({
        "prediction_id": prediction_id,
        "station_id": request.station_id,
        "predicted_at": predicted_at,
        "feature_fetched_at": latest_row["fetched_at"],
        "target_time": target_time,
        "predicted_bikes_available": prediction,
        "model_trained_at": metadata["trained_at"],
        **{f"feature_{c}": latest_row.get(c) for c in FEATURE_COLUMNS},
    })

    return PredictResponse(
        station_id=request.station_id,
        prediction_id=prediction_id,
        predicted_at=predicted_at,
        target_time=target_time,
        predicted_bikes_available=round(prediction, 2),
        confidence_interval_low=round(max(0.0, ci_low), 2),
        confidence_interval_high=round(ci_high, 2),
        model_trained_at=metadata["trained_at"],
        warning=warning,
    )
