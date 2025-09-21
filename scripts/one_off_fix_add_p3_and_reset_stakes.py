import os
from sqlalchemy import create_engine, text
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")
engine = create_engine(DATABASE_URL, echo=True, future=True)
with engine.begin() as conn:
    # --- 1. Add p3_user_id and num_players columns if missing ---
    try:
        conn.execute(text("""
            ALTER TABLE matches ADD COLUMN IF NOT EXISTS p3_user_id INTEGER REFERENCES users(id)
        """))
        conn.execute(text("""
            ALTER TABLE matches ADD COLUMN IF NOT EXISTS num_players INTEGER NOT NULL DEFAULT 2
        """))
        print(":white_check_mark: Added p3_user_id and num_players columns (if not existing).")
    except Exception as e:
        print(f":warning: Skipped adding columns: {e}")
    # --- 2. Reset stakes table to defaults ---
    try:
        conn.execute(text("DELETE FROM stakes"))
        conn.execute(text("""
            INSERT INTO stakes (stake_amount, entry_fee, winner_payout, label)
            VALUES
              (0, 0, 0, 'Free Play'),
              (4, 2, 4, '₹4 Stage'),
              (8, 4, 8, '₹8 Stage'),
              (12, 6, 12, '₹12 Stage')
        """))
        print(":white_check_mark: Reset stakes table with default values.")
    except Exception as e:
        print(f":warning: Skipped resetting stakes: {e}")
print(":tada: One-off migration completed successfully.")





