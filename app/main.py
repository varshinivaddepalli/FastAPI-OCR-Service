from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.session import Base, engine
from app.db import models  # ensure models are registered
from app.routers.upload import router as upload_router

# Create tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FastAPI OCR Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload_router, prefix="/api", tags=["upload"])

@app.get("/")
def root():
    return {"status": "ok"}
