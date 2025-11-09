import os
import logging
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

# -----------------------------
# Base ORM
# -----------------------------
Base = declarative_base()

# -----------------------------
# Normalize Database URL
# -----------------------------
def _normalize_db_url(raw: str) -> str:
    """
    Accepts:
      - postgresql://user:pass@host/db
      - postgresql+psycopg://user:pass@host/db
    Ensures driver psycopg and sslmode=require are present.
    """
    if not raw:
        raise RuntimeError("DATABASE_URL is not set. Configure it in Render/Env.")
    # Force psycopg driver
    raw = raw.replace("postgresql://", "postgresql+psycopg://")
    u = urlparse(raw)
    q = parse_qs(u.query)
    q.setdefault("sslmode", ["require"])
    new_q = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))

# -----------------------------
# Build Engine
# -----------------------------
DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))

# Disable SQLAlchemy engine logs
import logging
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool,
    echo=False,        # disable SQL echo
    future=True,
)


# -----------------------------
# Session
# -----------------------------
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True
)

# -----------------------------
# Dependency
# -----------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
