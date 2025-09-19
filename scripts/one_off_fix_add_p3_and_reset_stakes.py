from sqlalchemy import create_engine, text
import os
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")
engine = create_engine(DATABASE_URL, echo=False, future=True)
def run():
    with engine.begin() as conn:
        # ------------------------------
        # 1. Ensure p3_user_id in matches
        # ------------------------------
        print(":hammer_and_wrench: Ensuring p3_user_id exists in matches...")
        conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'matches' AND column_name = 'p3_user_id'
                ) THEN
                    ALTER TABLE matches
                    ADD COLUMN p3_user_id INTEGER REFERENCES users(id);
                END IF;
            END$$;
        """))
        print(":white_check_mark: p3_user_id ensured in matches table")
        # ------------------------------
        # 2. Drop + recreate stakes table
        # ------------------------------
        print(":arrows_counterclockwise: Dropping and recreating stakes table...")
        conn.execute(text("DROP TABLE IF EXISTS stakes CASCADE;"))
        conn.execute(text("""
            CREATE TABLE stakes (
                id SERIAL PRIMARY KEY,
                stake_amount INTEGER NOT NULL UNIQUE,
                entry_fee INTEGER NOT NULL,
                winner_payout INTEGER NOT NULL,
                label VARCHAR NOT NULL
            );
        """))
        print(":white_check_mark: stakes table recreated with correct schema")
        # ------------------------------
        # 3. Seed default rows
        # ------------------------------
        conn.execute(text("""
            INSERT INTO stakes (stake_amount, entry_fee, winner_payout, label) VALUES
                (0, 0, 0, 'Free Play'),
                (4, 2, 4, '₹4 Bounty'),
                (8, 4, 8, '₹8 Bounty'),
                (20, 10, 20, '₹20 Bounty');
        """))
        print(":package: stakes table seeded with Free/4/8/20 rules")
    print(":tada: Migration complete — p3_user_id ensured, stakes table reset!")
if __name__ == "__main__":
    run()





