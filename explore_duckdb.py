import duckdb

# Connect to your database file
con = duckdb.connect("bikeshare.duckdb")

# List all tables
print("Tables in database:")
print(con.execute("SHOW TABLES").fetchall())

# Preview first 5 rows of dim_station
print("\nPreview of dim_station:")
print(con.execute("SELECT * FROM dim_station LIMIT 5").fetchdf())

# Preview first 5 rows of fact_trips (if it exists)
print("\nPreview of fact_trips:")
print(con.execute("SELECT * FROM fact_trips LIMIT 5").fetchdf())
