from sqlalchemy import inspect
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, ForeignKey
from database import engine, Base
from models import GameMatch, Stake
# --- Ensure p3_user_id column exists in matches ---
with engine.connect() as conn:
    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns("matches")]
    if "p3_user_id" not in cols:
        print("[INFO] Adding p3_user_id to matches table...")
        conn.execute("ALTER TABLE matches ADD COLUMN p3_user_id INTEGER REFERENCES users(id)")
        conn.commit()
    else:
        print(":white_check_mark: p3_user_id already exists")
# --- Ensure stakes table exists ---
Base.metadata.create_all(bind=engine, tables=[Stake.__table__])
print(":white_check_mark: stakes table ensured with ORM model")
# --- Reset stakes with new rules ---
with Session(engine) as db:
    db.query(Stake).delete()  # clear old rules
    stakes = [
        Stake(stake_amount=0, entry_fee=0, winner_payout=0, label="Free Play"),
        Stake(stake_amount=4, entry_fee=2, winner_payout=4, label="₹4 Bounty"),
        Stake(stake_amount=8, entry_fee=4, winner_payout=8, label="₹8 Bounty"),
        Stake(stake_amount=20, entry_fee=10, winner_payout=20, label="₹20 Bounty"),
    ]
    db.add_all(stakes)
    db.commit()
    print(":white_check_mark: stakes table seeded with Free/4/8/20 bounty rules")
print(":tada: Migration complete")



