from sqlalchemy import create_engine, text
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")
# psycopg driver expected
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")
engine = create_engine(DATABASE_URL, future=True)
with engine.begin() as conn:
    print("Connecting…")
    # :white_check_mark: Rename old columns if they exist
    print("Checking and renaming columns in matches table…")
    try:
        conn.execute(text("""
            ALTER TABLE matches RENAME COLUMN p1_id TO p1_user_id;
        """))
        print("Renamed p1_id -> p1_user_id")
    except Exception as e:
        print(f"Skipped p1_id -> p1_user_id: {e}")
    try:
        conn.execute(text("""
            ALTER TABLE matches RENAME COLUMN p2_id TO p2_user_id;
        """))
        print("Renamed p2_id -> p2_user_id")
    except Exception as e:
        print(f"Skipped p2_id -> p2_user_id: {e}")
    # :white_check_mark: Ensure winner_user_id exists
    conn.execute(text("""
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS winner_user_id INTEGER;
    """))
    # :white_check_mark: Ensure system_fee exists
    conn.execute(text("""
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS system_fee NUMERIC(10,2) DEFAULT 0;
    """))
    # :white_check_mark: Ensure finished_at exists
    conn.execute(text("""
        ALTER TABLE matches ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;
    """))
    print("Migration done.")
