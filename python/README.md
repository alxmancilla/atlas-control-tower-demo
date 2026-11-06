# FastAPI implementation

This directory is the in-progress Python recreation of the tagged Node `0.1` reference application. It reads the same non-production Atlas demo database but does not yet serve the browser UI or implement write workflows.

## Run

From this directory, use the root `.env` without copying its credentials:

`uv run --env-file ../.env python -m app.run`

The Python service defaults to port `8000`; set `PYTHON_PORT` to override it. It enforces the same Atlas-only and demo-database-name guards as the Node reference.

## Initial parity endpoints

- `GET /health`
- `GET /api/status`
- `GET /api/profiles`
- `GET /api/overview?profile=retail`
- `GET /api/portfolio?profile=retail`
- `GET /api/kpis?profile=retail`

The overview and portfolio routes run their own MongoDB aggregations. The KPI route reads an existing materialized snapshot when present; otherwise it calculates an equivalent read-only response. Writing the `$merge` refresh pipeline is a later porting step.