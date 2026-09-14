# Bike Availability Prediction API — containerized for deployment to
# Fly.io, HF Spaces, Render, or any Docker-compatible host.
#
# Build:  docker build -t bikeshare-api .
# Run:    docker run -p 8000:8000 bikeshare-api
#
# This BAKES IN whatever is currently in your local models/ and
# dbt/bikeshare.duckdb at build time — a local `docker build` uses your
# actual filesystem, not what's committed to git, so this works even
# though both paths are .gitignored (correctly — they're generated
# artifacts, not source). The deployed image is a SNAPSHOT frozen at
# build time; rebuild and redeploy whenever you want it to reflect
# fresh data/a freshly retrained model, rather than it updating itself
# automatically.

FROM python:3.11-slim

WORKDIR /app

# LightGBM's compiled library depends on libgomp (GNU OpenMP runtime),
# which the "slim" base image strips out by default — without this,
# `import lightgbm` fails at container startup with
# "OSError: libgomp.so.1: cannot open shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

# Baked in from your local build context — see note above.
COPY models/ models/
COPY dbt/bikeshare.duckdb dbt/bikeshare.duckdb

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "src.serving.api:app", "--host", "0.0.0.0", "--port", "8000"]
