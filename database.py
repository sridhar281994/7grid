import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

Base = declarative_base()


def _normalize_db_url(raw: str) -> str:
    """
    Accepts:
      - postgresql://user:pass@host/db
      - postgresql+psycopg://user:pass@host/db
    Ensures driver psycopg and sslmode=require are present.
    """
    if not raw:
        raise RuntimeError("DATABASE_URL is not set. Configure it in Render/Env.")
    # force psycopg driver
    raw = raw.replace("postgresql://", "postgresql+psycopg://")
    u = urlparse(raw)
    q = parse_qs(u.query)
    q.setdefault("sslmode", ["require"])
    new_q = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL", ""))

engine = create_engine(
    DATABASE_URL,
    poolclass=NullPool, # <-- No pooling, avoid Render stale connections
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
