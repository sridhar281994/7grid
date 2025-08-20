import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import Base, engine
from models import *  # ensure models are imported so metadata is complete
from auth import router as auth_router
from game import router as game_router
from transactions import router as wallet_router

app = FastAPI(title="7grid Spin API", version="1.0.0")

# CORS (allow your app origins; add your Kivy / web URL if needed)
origins = [
    "*",  # tighten this later (e.g., http://localhost:3000 or your app URL)
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auto-create tables (Render first deploy): OK for MVP
Base.metadata.create_all(bind=engine)

@app.get("/")
def root():
    return {"ok": True, "service": "7grid", "docs": "/docs"}

@app.get("/healthz")
def health():
    return {"status": "ok"}

# Routers
app.include_router(auth_router)
app.include_router(wallet_router)
app.include_router(game_router)
