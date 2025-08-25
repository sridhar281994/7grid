from fastapi import FastAPI
from database import Base, engine
from models import *  # ensure models are imported so metadata knows them

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.on_event("startup")
def on_startup():
    # Safe to run on startup; avoids failing at module import time
    Base.metadata.create_all(bind=engine)
