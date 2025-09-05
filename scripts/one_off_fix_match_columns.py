"""
One-off migration script to add turn tracking columns to matches table.
Safe to run multiple times.
"""

import os
from sqlalchemy import create_engine, text


def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")

    # Force psycopg3 driver if psycopg[binary] is installed
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    print("Connecting…")
    engine = create_engine(db_url, future=True)

    stmts = [
        (
            "Ensure last_roll column",
            """ALTER TABLE matches ADD COLUMN IF NOT EXISTS last_roll INTEGER;""",
        ),
        (
            "Ensure current_turn column",
            """ALTER TABLE matches ADD COLUMN IF NOT EXISTS current_turn INTEGER;""",
        ),
    ]

    with engine.begin() as conn:
        for label, stmt in stmts:
            try:
                conn.execute(text(stmt))
                print(f"✅ {label}")
            except Exception as e:
                print(f"⚠️ Skipped {label}: {e}")

    print("🎉 Migration completed.")


if __name__ == "__main__":
    main()
