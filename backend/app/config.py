"""Configuration settings for pipeline hardening."""

import os
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    # Database
    database_url: str = "sqlite+aiosqlite:///./pipeline.db"

    # GitHub
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repo_owner: str = os.getenv("GITHUB_REPO_OWNER", "PROACTIVA-US")
    github_repo_name: str = os.getenv("GITHUB_REPO_NAME", "PipelineHardening")

    # Execution
    repo_path: str = str(Path(__file__).parent.parent.parent)  # Project root (backend -> app -> config.py)

    # Server
    host: str = "0.0.0.0"
    port: int = 8001

    class Config:
        env_file = ".env"


settings = Settings()
