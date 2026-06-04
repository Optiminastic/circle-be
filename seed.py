"""One-off, non-destructive seeding utility.

Loads `seed_data.json` into PostgreSQL, but only for tables that are currently
empty — existing data is never overwritten (honours "use my DB as-is"). Run it
once to populate a fresh database:

    python seed.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import get_settings
from app.db.database import Database
from app.domain.registry import RESOURCES, all_tables
from app.repositories.document_repository import SqlAlchemyDocumentRepository

SEED_PATH = Path(__file__).resolve().parent / "seed_data.json"


def main() -> None:
    settings = get_settings()
    if not settings.has_database:
        raise SystemExit("DATABASE_URL is not set. Add it to .env first.")

    database = Database(settings)
    database.connect()
    database.ensure_tables(all_tables())

    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    session = database.session()
    repo = SqlAlchemyDocumentRepository(session)

    inserted = 0
    for resource in RESOURCES.values():
        if repo.list(resource.table):
            print(f"skip   {resource.slug:<16} (already has data)")
            continue
        items = seed.get(resource.table, [])
        for idx, item in enumerate(items):
            rid = str(item.get(resource.id_field) or f"{resource.table}-{idx}")
            repo.upsert(resource.table, rid, item)
        inserted += len(items)
        print(f"seeded {resource.slug:<16} {len(items)} rows")

    session.close()
    database.dispose()
    print(f"\nDone. Inserted {inserted} rows.")


if __name__ == "__main__":
    main()
