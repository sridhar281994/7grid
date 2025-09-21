"""
One-off migration: Add profile_image column to users table if missing.
Usage: python scripts/one_off_fix_add_profile_image.py
"""
import os
import sys
from sqlalchemy import create_engine, inspect, text
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print(":x: DATABASE_URL not set")
    sys.exit(1)
engine = create_engine(DATABASE_URL)
def main():
    with engine.connect() as conn:
        inspector = inspect(conn)
        cols = [col["name"] for col in inspector.get_columns("users")]
        if "profile_image" not in cols:
            print(":zap: Adding profile_image column to users...")
            conn.execute(
                text("ALTER TABLE users ADD COLUMN profile_image VARCHAR DEFAULT 'assets/default.png'")
            )
            conn.commit()
            print(":white_check_mark: profile_image column added successfully.")
        else:
            print(":information_source: profile_image column already exists — skipping.")
if __name__ == "__main__":
    main()





