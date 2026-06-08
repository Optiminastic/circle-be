"""One-off: clear all demo data from the database, keeping only auth_users
(the akshae@ / hr@ login accounts + passwords). Run from the curcle-be dir:

    .\.venv\Scripts\python.exe scripts\clear_demo_data.py
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.domain.registry import all_tables

KEEP = {"auth_users"}


def main() -> None:
    settings = get_settings()
    if not settings.has_database:
        raise SystemExit("DATABASE_URL is not set.")
    engine = create_engine(settings.sqlalchemy_url)
    tables = [t for t in [*all_tables(), "documents"] if t not in KEEP]
    with engine.begin() as conn:
        for table in tables:
            conn.execute(text(f'TRUNCATE TABLE "{table}"'))
            print(f"cleared  {table}")
    print(f"kept     {', '.join(sorted(KEEP))}")
    engine.dispose()


if __name__ == "__main__":
    main()
