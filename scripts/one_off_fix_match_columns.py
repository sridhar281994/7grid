"""
One-off migration script to fix and extend the `matches` table.

- Renames old columns if needed
- Ensures winner_user_id, system_fee, finished_at
- NEW: Adds last_roll and turn columns for synced dice rolls
"""

import os
from sqlalchemy import create_engine, text


def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Force psycopg3 driver (psycopg[binary])
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    print("Connecting…")
    engine = create_engine(db_url, future=True)

    stmts = [
        # Old compatibility fixes
        ("Rename p1_id -> p1_user_id",
         "ALTER TABLE matches RENAME COLUMN p1_id TO p1_user_id;"),
        ("Rename p2_id -> p2_user_id",
         "ALTER TABLE matches RENAME COLUMN p2_id TO p2_user_id;"),

        # Core columns
        ("Ensure winner_user_id",
         "ALTER TABLE matches ADD COLUMN IF NOT EXISTS winner_user_id INTEGER;"),
        ("Ensure system_fee",
         "ALTER TABLE matches ADD COLUMN IF NOT EXISTS system_fee NUMERIC(10,2) DEFAULT 0;"),
        ("Ensure finished_at",
         "ALTER TABLE matches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;"),

        # 🔥 New columns for dice sync
        ("Ensure last_roll",
         "ALTER TABLE matches ADD COLUMN IF NOT EXISTS last_roll INTEGER;"),
        ("Ensure turn",
         "ALTER TABLE matches ADD COLUMN IF NOT EXISTS turn INTEGER DEFAULT 0;"),
    ]

    with engine.begin() as conn:
        for label, stmt in stmts:
            try:
                conn.execute(text(stmt))
                print(f":white_check_mark: {label}")
            except Exception as e:
                print(f":warning: Skipped {label}: {e}")

    print(":tada: Migration completed.")


if __name__ == "__main__":
    main()
