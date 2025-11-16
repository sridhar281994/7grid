import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from database import Base, engine, SessionLocal
from models import User
from routers import auth, users, wallet, game, match_routes

# NEW: agent pool
from routers.agent_pool import start_agent_pool


app = FastAPI(title="Spin Dice API", version="1.0.0")


# -------------------------------------------------
# CORS
# -------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Limit in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------------------------------
# Remove OLD bot logic — no more -1000/-1001 bots!
# -------------------------------------------------
def ensure_agent_users():
    """
    Optional helper: ensure the 20 agent accounts exist.
    If you already inserted them via SQL, this will just skip.
    """
    AGENT_IDS = list(range(10001, 10021))
    db = SessionLocal()
    try:
        for uid in AGENT_IDS:
            exists = db.query(User).filter(User.id == uid).first()
            if exists:
                continue
            # If missing, create a lightweight agent user
            db.add(
                User(
                    id=uid,
                    phone=f"agent_{uid}",
                    email=f"agent_{uid}@system.local",
                    password_hash="x",
                    name=f"Agent {uid}",
                    wallet_balance=100,
                    is_agent=True if hasattr(User, "is_agent") else False,
                )
            )
            print(f"[INIT] Created agent user {uid}")
        db.commit()
    finally:
        db.close()


# -------------------------------------------------
# Startup
# -------------------------------------------------
@app.on_event("startup")
async def on_startup():
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    print("✔ Database tables ready.")

    # Ensure the 20 agent players exist
    ensure_agent_users()

    # Init Redis
    from utils.redis_client import init_redis_with_retry
    await init_redis_with_retry(max_retries=5, delay=2.0)

    # Start agent pool background worker
    start_agent_pool()
    print("✔ Agent pool started.")


# -------------------------------------------------
# Basic routes
# -------------------------------------------------
@app.get("/")
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    return {"ok": True}


# -------------------------------------------------
# Routers
# -------------------------------------------------
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(wallet.router)
app.include_router(game.router)
app.include_router(match_routes.router)
