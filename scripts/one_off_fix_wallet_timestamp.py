import os
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")

with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        # add timestamp column if it doesn't exist
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'wallet_transactions'
                AND column_name = 'timestamp'
            ) THEN
                ALTER TABLE wallet_transactions
                ADD COLUMN timestamp TIMESTAMP DEFAULT NOW();
            END IF;
        END$$;
        """)
        conn.commit()

print("✅ Migration complete: wallet_transactions.timestamp ensured")
