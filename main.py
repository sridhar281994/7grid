import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from database import Base, engine, SessionLocal
from models import User
from routers import auth, users, wallet, game, match_routes
from routers.smart_agent_worker import start_agent_ai

# Import the agent pool function
from routers.agent_pool import start_agent_pool

app = FastAPI(title="Spin Dice API", version="1.0.0")

# -------------------------
# CORS
# -------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # :warning: TODO: restrict origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Startup helpers
# -------------------------
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
