import sys, os
sys.path.append(os.path.join(os.path.dirname(__file__), "..")) # add repo root

from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    # Ensure wallet_transactions.timestamp exists
    conn.execute(
        text(
            "ALTER TABLE wallet_transactions "
            "ADD COLUMN IF NOT EXISTS timestamp TIMESTAMPTZ DEFAULT now()"
        )
    )
    print("✅ wallet_transactions.timestamp ensured")

    # Ensure users.description exists
    conn.execute(
        text(
            "ALTER TABLE users "
            "ADD COLUMN IF NOT EXISTS description VARCHAR(255)"
        )
    )
    print("✅ users.description column ensured")

    conn.commit()
