from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")
engine = create_engine(DATABASE_URL, future=True)

def safe_exec(sql: str, desc: str):
    """Run a statement in its own transaction and log outcome."""
    with engine.begin() as conn:
        try:
            conn.execute(text(sql))
            print(f"✅ {desc}")
        except Exception as e:
            print(f"⚠️ Skipped {desc}: {e}")

print("Connecting…")

# Renames (safe to skip if already correct)
safe_exec("ALTER TABLE matches RENAME COLUMN p1_id TO p1_user_id;", "Rename p1_id -> p1_user_id")
safe_exec("ALTER TABLE matches RENAME COLUMN p2_id TO p2_user_id;", "Rename p2_id -> p2_user_id")

# Add missing columns
safe_exec("ALTER TABLE matches ADD COLUMN IF NOT EXISTS winner_user_id INTEGER;", "Ensure winner_user_id")
safe_exec("ALTER TABLE matches ADD COLUMN IF NOT EXISTS system_fee NUMERIC(10,2) DEFAULT 0;", "Ensure system_fee")
safe_exec("ALTER TABLE matches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;", "Ensure finished_at")

print("🎉 Migration completed.")
