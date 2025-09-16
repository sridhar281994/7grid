"""
One-off: seed wallet_balance for users (testing only).
Default behavior:
  - Set ALL users' wallet_balance = 100.00
Optional:
  - If SEED_USERS is provided (comma-separated list of emails or phones),
    only those users are updated.
Env:
  DATABASE_URL   - required
  SEED_AMOUNT    - optional (default "100.00")
  SEED_USERS     - optional (comma-separated emails/phones)
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
    seed_amount = os.getenv("SEED_AMOUNT", "100.00")
    seed_users = os.getenv("SEED_USERS", "").strip()
    print("Connecting to DB…")
    engine = create_engine(db_url, future=True)
    with engine.begin() as conn:
        if seed_users:
            # Update only specific users matched by email OR phone
            targets = [u.strip() for u in seed_users.split(",") if u.strip()]
            print(f"Targeted update for {len(targets)} users → amount={seed_amount}")
            # Use a temp table approach to safely bind many values
            conn.execute(text("""
                CREATE TEMP TABLE tmp_targets(v TEXT) ON COMMIT DROP;
            """))
            for t in targets:
                conn.execute(text("INSERT INTO tmp_targets(v) VALUES (:v)"), {"v": t})
            result = conn.execute(text(f"""
                UPDATE users
                SET wallet_balance = CAST(:amt AS NUMERIC(10,2))
                WHERE email IN (SELECT v FROM tmp_targets)
                   OR phone IN (SELECT v FROM tmp_targets);
            """), {"amt": seed_amount})
            print(f":white_check_mark: Updated rows: {result.rowcount or 0}")
        else:
            # Update ALL users
            print(f"Global update → ALL users set to {seed_amount}")
            result = conn.execute(text("""
                UPDATE users
                SET wallet_balance = CAST(:amt AS NUMERIC(10,2));
            """), {"amt": seed_amount})
            print(f":white_check_mark: Updated rows: {result.rowcount or 0}")
    print(":tada: Seeding completed.")
if __name__ == "__main__":
    main()
