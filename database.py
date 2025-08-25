from __future__ import annotations
import os
from typing import Generator, Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
# ----------------------------
# Environment / Config
# ----------------------------
RAW_URL: Optional[str] = os.getenv("DATABASE_URL")
if not RAW_URL:
    # Fallback to your provided DSN if env var is not set
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
    Make the URL acceptable for SQLAlchemy + psycopg2 on Render:
      - Convert 'postgres://' to 'postgresql://'
      - Ensure driver is psycopg2 (optional but explicit)
      - Force ?sslmode=require if not present
    """
    # 1) Fix old scheme 'postgres://'
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    # 2) Parse URL and query
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # 3) Force SSL if not explicitly set
    # Render PG requires SSL; psycopg2 uses ?sslmode=require
    if "sslmode" not in query:
        query["sslmode"] = "require"
    # 4) Ensure driver is psycopg2 (optional, but explicit helps logs & clarity)
    # Accept both 'postgresql://' and 'postgresql+psycopg2://'
    scheme = parsed.scheme
    if scheme == "postgresql":
        scheme = "postgresql+psycopg2"
    elif scheme.startswith("postgresql+") and "psycopg2" not in scheme:
        scheme = "postgresql+psycopg2"
    # 5) Rebuild URL
    rebuilt = urlunparse(
        (
            scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )
    return rebuilt
DATABASE_URL = _normalize_database_url(RAW_URL)
# ----------------------------
# Engine / Session / Base
# ----------------------------
engine = create_engine(
    DATABASE_URL,
    echo=ECHO,
    pool_pre_ping=POOL_PRE_PING,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_recycle=POOL_RECYCLE,
    future=True,  # SQLAlchemy 2.x style
)
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    future=True,
)
Base = declarative_base()
def get_db() -> Generator:
    """
    FastAPI dependency:
        from fastapi import Depends
        from database import get_db
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()





