import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        # make sure dialect is psycopg2
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url

RAW_DB_URL = os.getenv(
    "DATABASE_URL",
    # fallback (your external DB)
    "postgresql://spin_db_user:gha2FPMzzfwVFaPC88yzihY9MjBtkPgT@dpg-d1v8s9emcj7s73f6bemg-a.virginia-postgres.render.com/spin_db"
)
DATABASE_URL = _normalize_url(RAW_DB_URL)

ECHO = os.getenv("SQL_ECHO", "0") == "1"

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    echo=ECHO,
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
