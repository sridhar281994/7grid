import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

def _normalize_url(url: str) -> str:
    # Render sometimes gives postgres:// — SQLAlchemy expects postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Force psycopg2 driver (you installed psycopg2-binary)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    # Ensure SSL
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url

# Use env var on Render; fallback to your provided DSN
RAW_DB_URL = os.getenv(
    "DATABASE_URL",
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
    # connect_args optional with psycopg2 when sslmode in URL,
    # but harmless if present:
    connect_args={}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependency for routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
