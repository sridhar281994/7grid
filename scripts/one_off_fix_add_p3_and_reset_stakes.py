from sqlalchemy import create_engine, text
import os
# :white_check_mark: Render/GitHub Actions will provide DATABASE_URL in env
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set in environment")
engine = create_engine(DATABASE_URL, echo=True, future=True)
with engine.begin() as conn:
    # --- 1) Add p3_user_id column if not exists ---
    conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='matches'
                AND column_name='p3_user_id'
            ) THEN
                ALTER TABLE matches ADD COLUMN p3_user_id INTEGER REFERENCES users(id);
            END IF;
        END$$;
    """))
    # --- 2) Reset stakes table ---
    conn.execute(text("TRUNCATE TABLE stakes RESTART IDENTITY CASCADE;"))
    conn.execute(text("""
        INSERT INTO stakes (amount) VALUES (2), (4), (10);
    """))
print(":white_check_mark: Migration complete: p3_user_id ensured, stakes reset to (2, 4, 10)")
