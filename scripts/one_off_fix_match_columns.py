from sqlalchemy import create_engine, text
import os

def _normalize_db_url(url: str) -> str:
    # Render / Heroku style URLs may start with postgres:// instead of postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def main():
    raw_url = os.getenv("DATABASE_URL", "")
    if not raw_url:
        raise RuntimeError("DATABASE_URL not set")
    db_url = _normalize_db_url(raw_url)

    engine = create_engine(db_url, future=True)

    with engine.begin() as conn:
        print("🔄 Adding last_roll column to matches table…")
        conn.execute(text("""
            ALTER TABLE matches
            ADD COLUMN IF NOT EXISTS last_roll INTEGER;
        """))
        print("✅ Migration completed successfully")

if __name__ == "__main__":
    main()
