from sqlalchemy import create_engine, text
import os
from database import _normalize_db_url

def main():
    raw_url = os.getenv("DATABASE_URL", "")
    if not raw_url:
        raise RuntimeError("DATABASE_URL not set")
    db_url = _normalize_db_url(raw_url)
    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        print(":arrows_counterclockwise: Adding last_roll column to matches table…")
        conn.execute(text("""
            ALTER TABLE matches
            ADD COLUMN IF NOT EXISTS last_roll INTEGER;
        """))
        print(":white_check_mark: Migration completed successfully")
        
if __name__ == "__main__":
    main()
