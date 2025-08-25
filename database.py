from __future__ import annotations
import os
from typing import Generator, Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
RAW_URL: Optional[str] = os.getenv("DATABASE_URL")
if not RAW_URL:
    RAW_URL = (
        "postgresql://spin_db_user:gha2FPMzzfwVFaPC88yzihY9MjBtkPgT"
        "@dpg-d1v8s9emcj7s73f6bemg-a.virginia-postgres.render.com/spin_db"
    )
ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"
POOL_SIZE = int(os.getenv("SQL_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("SQL_MAX_OVERFLOW", "5"))
POOL_RECYCLE = int(os.getenv("SQL_POOL_RECYCLE", "1800"))  # seconds
POOL_PRE_PING = os.getenv("SQL_POOL_PRE_PING", "true").lower() == "true"
def _normalize_database_url(url: str) -> str:
    """
    Prepare URL for SQLAlchemy + psycopg (v3) on Render:
      - Convert 'postgres://' -> 'postgresql://'
      - Use driver 'postgresql+psycopg://'
      - Force ?sslmode=require if not present
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "sslmode" not in query:
        query["sslmode"] = "require"
    scheme = parsed.scheme
    # force psycopg v3 driver
    if scheme == "postgresql":
        scheme = "postgresql+psycopg"
    elif scheme.startswith("postgresql+") and "psycopg" not in scheme:
        scheme = "postgresql+psycopg"
    return urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )
DATABASE_URL = _normalize_database_url(RAW_URL)
engine = create_engine(
    DATABASE_URL,
    echo=ECHO,
    pool_pre_ping=POOL_PRE_PING,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_recycle=POOL_RECYCLE,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()
def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
