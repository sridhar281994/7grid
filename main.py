from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from pathlib import Path
from database import Base, engine
from routers import auth, users, wallet, game
import logging
log = logging.getLogger("uvicorn.error")
app = FastAPI(title="Spin Dice API", version="1.0.0")
# --- CORS (tighten in production) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # e.g. ["https://yourapp.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- Root: redirect to interactive docs ---
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
# --- Health checks ---
@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True}
@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}
# --- Favicon to avoid 404 noise (optional file) ---
FAVICON_PATH = Path("static/favicon.ico")
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    if FAVICON_PATH.exists():
        return FileResponse(FAVICON_PATH)
    # No favicon present; return no-content instead of 404
    return JSONResponse(status_code=204, content={})
# --- Startup: create tables (use migrations in prod if you add Alembic) ---
@app.on_event("startup")
def on_startup():
    try:
        Base.metadata.create_all(bind=engine)
        log.info("Database tables ensured/created.")
    except Exception as e:
        log.exception("Failed to create tables: %s", e)
        # Don't crash the app; keep serving /health and docs
# --- Routers ---
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(wallet.router)
app.include_router(game.router)





