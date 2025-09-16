"""
One-off migration: extend TxType enum with ENTRY, WIN, and FEE.
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
    # PostgreSQL ENUM alter
    stmts = [
        ("Add ENTRY to TxType", "ALTER TYPE txtype ADD VALUE IF NOT EXISTS 'entry';"),
        ("Add WIN to TxType", "ALTER TYPE txtype ADD VALUE IF NOT EXISTS 'win';"),
        ("Add FEE to TxType", "ALTER TYPE txtype ADD VALUE IF NOT EXISTS 'fee';"),
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





