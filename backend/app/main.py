"""
Pipeline Hardening - Minimal Autonomous Execution Pipeline

This is an isolated environment for proving the autonomous execution pipeline
works reliably before integrating with larger systems.

Features:
- FastAPI backend with health endpoint
- SQLite database for execution state
- Plan parser for markdown task plans
- Task executor using Claude Code CLI
- PR workflow (branch -> commit -> PR -> merge)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import init_db
from app.routers.autonomous import router as autonomous_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

    yield

    # Shutdown
    logger.info("Shutting down...")


app = FastAPI(
    title="Pipeline Hardening",
    description="Minimal autonomous execution pipeline for E2E testing",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(autonomous_router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Pipeline Hardening",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
