from database import engine, Base
from sqlalchemy import text
with engine.begin() as conn:
    # :white_check_mark: Add new column if not exists
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
print(":white_check_mark: p3_user_id column ensured in matches table")
