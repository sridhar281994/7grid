from sqlalchemy import text
from database import engine
if __name__ == "__main__":
    print(":arrows_counterclockwise: Adding desc column to users table if not exists...")
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS description VARCHAR(50);
        """))
        conn.commit()
    print(":white_check_mark: Migration complete: users.description column ensured (max 50 chars).")





