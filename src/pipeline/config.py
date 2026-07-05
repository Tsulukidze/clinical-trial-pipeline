"""Central configuration.

I read all settings from environment variables. This way the same
code runs on my machine, inside Docker, or anywhere else - only the
environment changes, never the code.

For local work I also load a .env file if one exists. Inside Docker
the variables come from docker-compose, so the .env load simply does
nothing there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # does nothing if there is no .env file


@dataclass(frozen=True)
class Settings:
    # Database connection. The defaults match docker-compose,
    # so everything works out of the box.
    pg_user: str = os.getenv("POSTGRES_USER", "ctp_user")
    pg_password: str = os.getenv("POSTGRES_PASSWORD", "ctp_password")
    pg_host: str = os.getenv("POSTGRES_HOST", "localhost")
    pg_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    pg_db: str = os.getenv("POSTGRES_DB", "clinical_trials")

    # ClinicalTrials.gov API v2 settings.
    ctgov_base_url: str = os.getenv(
        "CTGOV_BASE_URL", "https://clinicaltrials.gov/api/v2/studies"
    )
    ctgov_page_size: int = int(os.getenv("CTGOV_PAGE_SIZE", "100"))
    ctgov_timeout_seconds: int = int(os.getenv("CTGOV_TIMEOUT", "30"))

    @property
    def database_url(self) -> str:
        """Build the full connection URL from the parts above."""
        return (
            f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )


# One shared settings object for the whole application.
settings = Settings()
