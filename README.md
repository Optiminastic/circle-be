# Curcle HRMS — Backend (FastAPI + PostgreSQL)

Source of truth for all Curcle HR data. Built as a small, layered, SOLID
codebase so it scales and stays maintainable.

## Architecture

```
app/
  core/         config (env settings), logging, error hierarchy + handlers
  db/           Database boundary (engine, sessions, healthcheck, DDL)
  domain/       resource registry (one declaration per resource)
  repositories/ DocumentRepository protocol + PostgreSQL/JSONB implementation
  services/     ResourceService (business rules: id-gen, not-found, patch/merge)
  api/          dependencies (DI wiring) + thin routers (meta, resources)
  main.py       composition root (app factory + lifespan)
```

- **SRP** — every module has one job. **OCP** — add a resource by adding one line
  in `domain/registry.py`. **LSP/ISP** — small `DocumentRepository` Protocol.
  **DIP** — services/routes depend on the abstraction; the SQLAlchemy
  implementation is injected via FastAPI dependencies.
- **Error tolerance** — DB failures are normalized to typed errors and returned
  as structured JSON; a missing/unreachable DB degrades to `503` instead of
  crashing; sessions are always closed.

Each resource is a Postgres table of `(id TEXT PK, data JSONB, created_at,
updated_at)`. JSONB keeps the nested HR documents flexible yet queryable.

## Run

```bash
cd curcle-be
python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -r requirements.txt
copy .env.example .env                  # then set DATABASE_URL (PostgreSQL)
uvicorn app.main:app --reload --port 8000
```

- Root: http://localhost:8000/ · Docs: http://localhost:8000/docs
- Health (incl. DB): http://localhost:8000/api/health

## Seeding (optional, non-destructive)

```bash
python seed.py
```

Loads `seed_data.json` into Postgres **only for tables that are empty** — your
existing data is never overwritten.

## REST surface

| Method | Path | |
| --- | --- | --- |
| GET | `/api/{resource}` | list |
| GET | `/api/{resource}/{id}` | get one |
| POST | `/api/{resource}` | create |
| PUT | `/api/{resource}/{id}` | replace |
| PATCH | `/api/{resource}/{id}` | merge-update |
| DELETE | `/api/{resource}/{id}` | delete (204) |

Resources: `candidates, interviews, iq-tests, assignments, bgvs, onboarding,
employees, assets, email-templates, sent-emails, offboarding`.
PKs: most `id`; `bgvs`/`onboarding` use `candidateId`, `offboarding` uses `employeeId`.

## Config (`.env`)

| Var | Default | |
| --- | --- | --- |
| `DATABASE_URL` | — | PostgreSQL URL (`postgresql://…`) |
| `AUTO_CREATE_TABLES` | `true` | create resource tables if missing |
| `CORS_ORIGINS` | localhost:3000,3001 | comma-separated allowed origins |
| `LOG_LEVEL` | `INFO` | |
