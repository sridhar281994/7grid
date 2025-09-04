# scripts/one_off_fix_match_columns.py
"""
One-off migration script to fix columns in the matches table.
Safe for psycopg3 (psycopg[binary]) environments.
"""

import os
from sqlalchemy import create_engine, text

def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Force psycopg3 driver
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    print("Connecting…")
    engine = create_engine(db_url, future=True, isolation_level="AUTOCOMMIT")

    stmts = [
        ("Rename p1_id -> p1_user_id",
         """ALTER TABLE matches RENAME COLUMN p1_id TO p1_user_id;"""),
        ("Rename p2_id -> p2_user_id",
         """ALTER TABLE matches RENAME COLUMN p2_id TO p2_user_id;"""),
        ("Ensure winner_user_id",
         """ALTER TABLE matches ADD COLUMN IF NOT EXISTS winner_user_id INTEGER;"""),
        ("Ensure system_fee",
         """ALTER TABLE matches ADD COLUMN IF NOT EXISTS system_fee NUMERIC(10,2) DEFAULT 0;"""),
        ("Ensure finished_at",
         """ALTER TABLE matches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;"""),
    ]

    with engine.connect() as conn:
        for label, stmt in stmts:
            try:
                conn.execute(text(stmt))
                print(f"✅ {label}")
            except Exception as e:
                print(f"⚠️ Skipped {label}: {e}")

    print("🎉 Migration completed.")

if __name__ == "__main__":
    main()
