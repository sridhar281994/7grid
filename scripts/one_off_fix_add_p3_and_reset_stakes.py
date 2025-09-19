from sqlalchemy import create_engine, text
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")
engine = create_engine(DATABASE_URL)
with engine.begin() as conn:
    # 1. Ensure p3_user_id exists on matches table
    conn.execute(text("""
        ALTER TABLE matches
        ADD COLUMN IF NOT EXISTS p3_user_id INTEGER REFERENCES users(id);
    """))
    print(":white_check_mark: p3_user_id ensured in matches table")
    # 2. Create stakes table with consistent schema
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS stakes (
            stake_amount INTEGER PRIMARY KEY,
            entry_fee NUMERIC(10,2) NOT NULL,
            winner_payout NUMERIC(10,2) NOT NULL
        );
    """))
    print(":white_check_mark: stakes table ensured with correct columns")
    # 3. Reset stakes and insert rules
    conn.execute(text("TRUNCATE TABLE stakes RESTART IDENTITY CASCADE;"))
    conn.execute(text("""
        INSERT INTO stakes (stake_amount, entry_fee, winner_payout) VALUES
            (0, 0, 0),   -- Free Play
            (4, 2, 4),   -- Stage 1
            (8, 4, 8),   -- Stage 2
            (20, 10, 20) -- Stage 3
        ON CONFLICT (stake_amount) DO NOTHING;
    """))
    print(":white_check_mark: stakes table reset with Free/4/8/20 rules")
print(":tada: Migration complete")


