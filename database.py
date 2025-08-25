import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.engine.url import make_url
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")
# Ensure SQLAlchemy uses psycopg (v3) even if env var is plain "postgresql://"
url = make_url(DATABASE_URL)
if url.drivername == "postgresql":          # e.g. postgresql://user:pass@host/db
    url = url.set(drivername="postgresql+psycopg")
ECHO = os.getenv("DEV_ECHO_SQL", "false").lower() == "true"
engine = create_engine(url, pool_pre_ping=True, echo=ECHO)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
