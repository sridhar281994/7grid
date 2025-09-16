"""
One-off migration: mark all matches for a user as finished.
"""
import os
from sqlalchemy import create_engine, text
def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        # Replace 123 with the user_id that is stuck
        conn.execute(text("UPDATE matches SET status='finished' WHERE (p1_user_id=123 OR p2_user_id=123) AND status='active';"))
    print(":white_check_mark: Cleaned up stuck matches.")
if __name__ == "__main__":
    main()





