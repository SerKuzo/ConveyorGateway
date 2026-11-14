import os
from dotenv import load_dotenv
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings

load_dotenv()

class Settings(BaseSettings):
    # База данных
    DB_HOST: str     = Field(...,  env="DB_HOST"    )
    DB_PORT: int     = Field(5432, env="DB_PORT"    )
    DB_NAME: str     = Field(...,  env="DB_NAME"    )
    DB_USER: str     = Field(...,  env="DB_USER"    )
    DB_PASSWORD: str = Field(...,  env="DB_PASSWORD")

    # Пути
    WATCH_DIR: str   = Field("/srv/conveyor_share",   env="WATCH_DIR"  )
    ARCHIVE_DIR: str = Field("/srv/conveyor_archive", env="ARCHIVE_DIR")
    FAILED_DIR: str  = Field("/srv/conveyor_failed",  env="FAILED_DIR" )

    # Настройки воркеров
    WORKER_COUNT: int         = Field(3,   env="WORKER_COUNT" )
    POLL_INTERVAL: float = Field(1.0, env="POLL_INTERVAL")

try:
    settings = Settings()
except ValidationError as e:
    print(f"Ошибка конфигурации: {e}")
    exit(1)