"""
Exports the latest known state of every station to frontend_data.json,
so index.html can render it on the map + table.

Run this from the project root (bikeshare-platform/), inside your venv:
    python export_for_frontend.py

If you've only run poll_station_status.py so far, you'll get bike/dock
counts but no station names or lat/lon (those live in station_information,
a separate poller). Run this first for full data:
    python -m src.ingestion.poll_station_information
"""
import glob
import json

import duckdb

status_files = glob.glob("raw/station_status/**/*.parquet", recursive=True)
if not status_files:
    raise SystemExit(
        "No station_status data found. Run:\n"
        "  python -m src.ingestion.poll_station_status"
    )

# Latest reading per station. Adjust 'last_reported' below if your
# schema names this column differently (check with: df.columns).
status_df = duckdb.sql(f"""
    SELECT *
    FROM read_parquet({status_files!r})
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY station_id ORDER BY last_reported DESC
    ) = 1
""").df()

info_files = glob.glob("raw/station_information/**/*.parquet", recursive=True)

if info_files:
    info_df = duckdb.sql(f"""
        SELECT *
        FROM read_parquet({info_files!r})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY station_id ORDER BY dt DESC
        ) = 1
    """).df()
    merged = status_df.merge(
        info_df[["station_id", "name", "lat", "lon", "capacity"]],
        on="station_id",
        how="left",
    )
else:
    print(
        "No station_information found — map will have no coordinates. "
        "Run: python -m src.ingestion.poll_station_information"
    )
    merged = status_df

# pandas represents missing values as NaN, which Python's json module
# writes out as a bare `NaN` token — invalid JSON that browsers reject.
# Converting to object dtype first is required: on a float column,
# pandas silently casts None back into NaN, so .where() alone doesn't
# stick until the column can actually hold None.
merged = merged.astype(object).where(merged.notnull(), None)

records = merged.to_dict(orient="records")

with open("frontend_data.json", "w") as f:
    json.dump(records, f, default=str, allow_nan=False)

print(f"Wrote {len(records)} stations to frontend_data.json")
print(f"Columns: {list(merged.columns)}")
