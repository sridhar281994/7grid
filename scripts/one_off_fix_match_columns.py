"""
One-off migration: add wallet_balance column to users and create transactions table.
"""
import os
from sqlalchemy import create_engine, text
def main():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    # psycopg3 compatibility
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    print("Connecting to DB…")
    engine = create_engine(db_url, future=True)
    stmts = [
        (
            "Ensure wallet_balance in users",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance FLOAT DEFAULT 0;"
        ),
        (
            "Create transactions table",
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                amount FLOAT NOT NULL,
                txn_type VARCHAR NOT NULL,
                status VARCHAR DEFAULT 'pending',
                provider_txn_id VARCHAR,
                created_at TIMESTAMP DEFAULT now()
            );
            """
        ),
    ]
    with engine.begin() as conn:
        for label, stmt in stmts:
            try:
                conn.execute(text(stmt))
                print(f":white_check_mark: {label}")
            except Exception as e:
                print(f":warning: Skipped {label}: {e}")
    print(":tada: Migration completed successfully.")
if __name__ == "__main__":
    main()
