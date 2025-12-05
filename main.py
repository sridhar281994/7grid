import asyncio
import os
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from database import Base, engine, SessionLocal
from models import User
from routers import auth, users, wallet, game, match_routes, wallet_portal, admin_wallet
from routers.smart_agent_worker import start_agent_ai

# Import the agent pool function
from routers.agent_pool import start_agent_pool

def _split_origins(raw: str | None) -> List[str]:
    if not raw:
        return []
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return parts or []


def _normalize_origin(origin: str) -> str:
    if origin.startswith(("http://", "https://")):
        return origin.rstrip("/")
    return origin


DEFAULT_WALLET_ORIGINS = ["https://wallet.srtech.co.in"]
LOCALHOST_ORIGINS = ["https://localhost", "http://localhost"]
DEFAULT_ORIGIN_REGEX = r"https://([a-z0-9-]+\.)?(srtech\.co\.in|onrender\.com)$"


def _build_allowed_origins() -> List[str]:
    """Build a deterministic CORS origin list with sensible defaults."""
    android_origin = os.getenv("ANDROID_APP_ORIGIN")
    wallet_origin = os.getenv("WALLET_WEB_ORIGIN")
    extra_origins = os.getenv("CORS_ALLOWED_ORIGINS")

    candidates: List[str] = []
    for raw in (
        _split_origins(extra_origins)
        + _split_origins(android_origin)
        + _split_origins(wallet_origin)
    ):
        normalized = _normalize_origin(raw)
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    if not candidates:
        normalized_defaults = [
            _normalize_origin(origin) for origin in DEFAULT_WALLET_ORIGINS
        ]
        return normalized_defaults + LOCALHOST_ORIGINS

    for fallback in LOCALHOST_ORIGINS:
        if fallback not in candidates:
            candidates.append(fallback)

    return candidates


def _build_allowed_origin_regex() -> str:
    """Regex fallback that covers managed domains like Render."""
    env_regex = (os.getenv("CORS_ALLOWED_REGEX") or "").strip()
    return env_regex or DEFAULT_ORIGIN_REGEX


app = FastAPI(title="Spin Dice API", version="1.0.0")

ALLOWED_ORIGINS = _build_allowed_origins()
ALLOWED_ORIGIN_REGEX = _build_allowed_origin_regex()

# -------------------------
# CORS
# -------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_context(request: Request, call_next):
    request.state.client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    request.state.user_agent = request.headers.get("user-agent")
    request.state.device_fingerprint = request.headers.get("x-device-fingerprint")
    response = await call_next(request)
    return response

# -------------------------
# Startup helpers
# -------------------------
def ensure_paypal_column():
    """Backfill paypal_id column in legacy databases."""
    ddl = text("ALTER TABLE users ADD COLUMN IF NOT EXISTS paypal_id VARCHAR(255)")
    with engine.begin() as conn:
        conn.execute(ddl)
    print(":white_check_mark: Ensured users.paypal_id column exists.")


def ensure_bots():
    """Insert bot users (-1000, -1001, -1002) into DB if missing."""
    db = SessionLocal()
    try:
        bots = [
            {
                "id": -1000,
                "phone": "bot_sharp",
                "email": "bot_sharp@system.local",
                "password_hash": "x",
                "name": "Sharp (Bot)",
            },
            {
                "id": -1001,
                "phone": "bot_crazy",
                "email": "bot_crazy@system.local",
                "password_hash": "x",
                "name": "Crazy Boy (Bot)",
            },
            {
                "id": -1002,
                "phone": "bot_srtech",
                "email": "bot_srtech@system.local",
                "password_hash": "x",
                "name": "SRTech Bot",
            },
        ]
        for bot in bots:
            exists = db.query(User).filter(User.id == bot["id"]).first()
            if not exists:
                db.add(User(**bot))
                print(f"[INIT] Inserted bot user: {bot['name']} (id={bot['id']})")
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
async def on_startup():
    # Ensure DB tables
    Base.metadata.create_all(bind=engine)
    print(":white_check_mark: Database tables ensured/created.")

    # Backfill paypal column for legacy databases
    ensure_paypal_column()

    # Insert bot rows
    ensure_bots()

    # Redis warm-up
    from utils.redis_client import init_redis_with_retry
    await init_redis_with_retry(max_retries=5, delay=2.0)

    # Start the agent pool (background task for agent filling in matches)
    start_agent_pool()
    start_agent_ai() 


# -------------------------
# Routes
# -------------------------
@app.get("/")
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    return {"ok": True}


# -------------------------
# Routers
# -------------------------
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(wallet.router)
app.include_router(game.router)
app.include_router(match_routes.router)
app.include_router(wallet_portal.router)
app.include_router(admin_wallet.router)
