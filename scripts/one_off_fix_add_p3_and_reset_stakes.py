from sqlalchemy import create_engine, text
from database import Base
from models import Stake
import os

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL)

with engine.begin() as conn:
    # ✅ Ensure p3_user_id exists
    conn.execute(text("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='matches' AND column_name='p3_user_id'
        ) THEN
            ALTER TABLE matches ADD COLUMN p3_user_id INTEGER REFERENCES users(id);
        END IF;
    END$$;
    """))
    print(":white_check_mark: p3_user_id ensured in matches table")

    # ✅ Drop & recreate stakes table
    conn.execute(text("DROP TABLE IF EXISTS stakes CASCADE;"))
    Base.metadata.tables["stakes"].create(bind=conn)
    print(":white_check_mark: stakes table recreated with entry_fee + payout")

    # ✅ Insert dynamic stake rules
    stakes = [
        Stake(stake_amount=0, entry_fee=0, winner_payout=0, label="Free Play"),
        Stake(stake_amount=4, entry_fee=2, winner_payout=4, label="₹4 Bounty"),
        Stake(stake_amount=8, entry_fee=4, winner_payout=8, label="₹8 Bounty"),
        Stake(stake_amount=20, entry_fee=10, winner_payout=20, label="₹20 Bounty"),
    ]
    for s in stakes:
        conn.execute(
            text("INSERT INTO stakes (stake_amount, entry_fee, winner_payout, label) VALUES (:a,:e,:w,:l)"),
            {"a": s.stake_amount, "e": s.entry_fee, "w": s.winner_payout, "l": s.label}
        )
    print(":white_check_mark: stakes seeded (Free/4/8/20)")
