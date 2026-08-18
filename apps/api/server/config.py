"""Application configuration."""

import dataclasses
import os
from pathlib import Path

from fastapi_mongo_base.core.config import Settings as BaseSettings


@dataclasses.dataclass
class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    project_name: str = "umedia-api"
    project_description: str = "Universal Media Manager"
    base_path: str = "/api/v1"
    root_url: str = "drive.uln.me"
    database_uri: str = dataclasses.field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "sqlite+aiosqlite:////data/umedia.sqlite3",
        ),
    )
    data_dir: Path = dataclasses.field(
        default_factory=lambda: Path(os.getenv("UMEDIA_DATA_DIR", "/data")),
    )
    master_key: str | None = dataclasses.field(
        default_factory=lambda: os.getenv("UMEDIA_MASTER_KEY"),
    )
