from sqlalchemy import create_engine, text
import os
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL not set")
engine = create_engine(DB_URL)
with engine.begin() as conn:
    # Ensure stakes table exists
    conn.execute(text("""
    CREATE TABLE IF NOT EXISTS stakes (
        id SERIAL PRIMARY KEY,
        stake_amount INTEGER UNIQUE NOT NULL,
        entry_fee NUMERIC(10,2) NOT NULL,
        winner_payout NUMERIC(10,2) NOT NULL
    );
    """))
    # Insert Free Stage if not exists
    conn.execute(text("""
    INSERT INTO stakes (stake_amount, entry_fee, winner_payout)
    VALUES (0, 0, 0)
    ON CONFLICT (stake_amount) DO NOTHING;
    """))
print(":white_check_mark: Free stage (0,0,0) ensured in stakes table")
