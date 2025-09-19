from sqlalchemy import create_engine, text
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")
engine = create_engine(DATABASE_URL)
with engine.begin() as conn:
    # 1. Ensure p3_user_id exists on matches
    conn.execute(text("""
        ALTER TABLE matches
        ADD COLUMN IF NOT EXISTS p3_user_id INTEGER REFERENCES users(id);
    """))
    print(":white_check_mark: p3_user_id ensured in matches table")
    # 2. Drop & recreate stakes table with correct schema
    conn.execute(text("DROP TABLE IF EXISTS stakes CASCADE;"))
    conn.execute(text("""
        CREATE TABLE stakes (
            stake_amount INTEGER PRIMARY KEY,
            entry_fee NUMERIC(10,2) NOT NULL,
            winner_payout NUMERIC(10,2) NOT NULL,
            label TEXT NOT NULL
        );
    """))
    print(":white_check_mark: stakes table recreated with label column")
    # 3. Insert rules with friendly labels
    conn.execute(text("""
        INSERT INTO stakes (stake_amount, entry_fee, winner_payout, label) VALUES
            (0, 0, 0, 'Free Play'),
            (4, 2, 4, '4rs Bounty'),
            (8, 4, 8, '8rs Bounty'),
            (20, 10, 20, '20rs Bounty');
    """))
    print(":white_check_mark: stakes table seeded with Free/4/8/20 rules and labels")
print(":tada: Migration complete")





