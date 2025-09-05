"""
One-off migration: ensure matches table has last_roll and current_turn.
"""

import os
from sqlalchemy import create_engine, text

def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")

    # psycopg3 compatibility
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    print("Connecting…")
    engine = create_engine(db_url, future=True)

    stmts = [
        ("Ensure last_roll", "ALTER TABLE matches ADD COLUMN IF NOT EXISTS last_roll INTEGER;"),
        ("Ensure current_turn", "ALTER TABLE matches ADD COLUMN IF NOT EXISTS current_turn INTEGER DEFAULT 0;"),
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
