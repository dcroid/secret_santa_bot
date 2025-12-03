import os
from dataclasses import dataclass


@dataclass
class Settings:
    bot_token: str
    admin_id: int
    budget: str
    db_host: str
    db_user: str
    db_password: str
    db_name: str


def load_settings() -> Settings:
    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        admin_id=int(os.environ["ADMIN_ID"]),
        budget=os.environ.get("ENV_BUDGET", "Не указан"),
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_user=os.environ.get("DB_USER", "root"),
        db_password=os.environ.get("DB_PASSWORD", ""),
        db_name=os.environ.get("DB_NAME", "secret_santa_db"),
    )
