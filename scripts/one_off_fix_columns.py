import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set")

# normalize for psycopg driver + sslmode=require if needed
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")
if "sslmode=" not in DATABASE_URL:
    sep = "&" if "?" in DATABASE_URL else "?"
    DATABASE_URL = f"{DATABASE_URL}{sep}sslmode=require"

engine = create_engine(DATABASE_URL, future=True)

SQLS = [
    # users.name
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS name VARCHAR;",
    # users.updated_at (your ORM selects it; keep it nullable)
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;",
    # wallet_transactions.provider_ref (router uses it)
    "ALTER TABLE wallet_transactions ADD COLUMN IF NOT EXISTS provider_ref VARCHAR;"
]

def run():
    print("Connecting…")
    with engine.begin() as conn:
        for stmt in SQLS:
            print(f"Running: {stmt}")
            conn.execute(text(stmt))
    print("Done.")

if __name__ == "__main__":
    run()
