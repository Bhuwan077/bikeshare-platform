# Bike Availability Prediction API — containerized for deployment to
# Fly.io, HF Spaces, Render, or any Docker-compatible host.
#
# Build:  docker build -t bikeshare-api .
# Run:    docker run -p 8000:8000 -v $(pwd)/models:/app/models -v $(pwd)/dbt:/app/dbt bikeshare-api
#
# The model (models/) and warehouse (dbt/bikeshare.duckdb) are NOT
# baked into the image — mount them as volumes, or build them into a
# deployment-specific image if your platform doesn't support volumes.
# This keeps the image small and means a retrained model doesn't
# require a full rebuild.

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "src.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
