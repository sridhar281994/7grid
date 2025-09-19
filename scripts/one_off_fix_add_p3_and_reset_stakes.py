from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, Enum, Numeric, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func
import os, enum
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
# -----------------------
# Enums
# -----------------------
class MatchStatus(enum.Enum):
    WAITING = "waiting"
    ACTIVE = "active"
    FINISHED = "finished"
# -----------------------
# Matches table (patched with p3_user_id)
# -----------------------
class GameMatch(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, nullable=False)
    p1_user_id = Column(Integer, ForeignKey("users.id"))
    p2_user_id = Column(Integer, ForeignKey("users.id"))
    p3_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # :white_check_mark: ensure exists
    winner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(Enum(MatchStatus), default=MatchStatus.WAITING, nullable=False)
    system_fee = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
# -----------------------
# Stakes table (new)
# -----------------------
class Stake(Base):
    __tablename__ = "stakes"
    id = Column(Integer, primary_key=True, index=True)
    stake_amount = Column(Integer, unique=True, nullable=False)  # each player's contribution
    entry_fee = Column(Integer, nullable=False)  # amount deducted per player
    winner_payout = Column(Integer, nullable=False)  # amount winner receives
    label = Column(String, nullable=False)  # label shown in frontend
# -----------------------
# Migration
# -----------------------
def run():
    print(":arrows_counterclockwise: Running migration...")
    Base.metadata.create_all(bind=engine)  # ensure tables/columns exist
    session = SessionLocal()
    try:
        # Reset stakes
        session.query(Stake).delete()
        session.commit()
        # Seed with rules
        stakes = [
            Stake(stake_amount=0, entry_fee=0, winner_payout=0, label="Free Play"),
            Stake(stake_amount=4, entry_fee=2, winner_payout=4, label="₹4 Bounty"),
            Stake(stake_amount=8, entry_fee=4, winner_payout=8, label="₹8 Bounty"),
            Stake(stake_amount=20, entry_fee=10, winner_payout=20, label="₹20 Bounty"),
        ]
        session.add_all(stakes)
        session.commit()
        print(":white_check_mark: p3_user_id ensured in matches table")
        print(":white_check_mark: stakes table recreated with Free/4/8/20 rules")
        print(":tada: Migration complete")
    finally:
        session.close()
if __name__ == "__main__":
    run()





