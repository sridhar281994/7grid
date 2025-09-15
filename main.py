from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from database import Base, engine
from routers import auth, users, wallet, game, match_routes

app = FastAPI(title="Spin Dice API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return RedirectResponse("/docs")

@app.get("/health")
def health():
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    print("Database tables ensured/created.")

    # Redis warm-up
    from utils.redis_client import init_redis_with_retry
    await init_redis_with_retry(max_retries=5, delay=2.0)

# Routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(wallet.router)
app.include_router(game.router)
app.include_router(match_routes.router)
