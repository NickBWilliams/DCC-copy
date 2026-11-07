# DCC Universal — Independent MVP

Standalone commercial dispatch proof-of-concept. No Terraboost credentials, data, or integrations.

## Local run

```bash
cd universal
python -m pip install -r requirements.txt
export DCC_ADMIN_KEY='set-a-strong-local-secret'
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000. Use organization `demo` and the configured API key. Routes persist to local SQLite by default. Set `DCC_DATABASE_URL` for a dedicated PostgreSQL database.

## Test

```bash
cd universal
pytest -q
```

## Current scope

Dashboard with ten route lifecycle tabs, organization-scoped routes, counteroffers, transition validation, optimistic version checking and audit events.

## Not yet production ready

Shared API key is not real tenant authentication. Dashboard simulates contractor decisions via admin actions, not secure contractor links. No actual email, routing engine, external assignment, billing, rate limits, migrations, or deployment. Do not expose this MVP publicly with real customer data. Legacy DCC files are not modified.
