import os
import time
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _database_url() -> str | URL:
    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    host = os.getenv("DB_HOST")
    if host:
        return URL.create(
            drivername="postgresql+psycopg",
            username=os.getenv("DB_USER", "microservices"),
            password=os.getenv("DB_PASSWORD", "development-only-change-me"),
            host=host,
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "microservices"),
        )

    return "sqlite:///./microservices.db"


DATABASE_URL = _database_url()
_is_sqlite = str(DATABASE_URL).startswith("sqlite")
_engine_options: dict[str, object] = {"pool_pre_ping": True}
if _is_sqlite:
    _engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables(tables: list) -> None:
    Base.metadata.create_all(bind=engine, tables=tables)


def initialize_database(tables: list, attempts: int = 30, delay_seconds: int = 2) -> None:
    last_error = None
    for attempt in range(attempts):
        try:
            create_tables(tables)
            return
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(delay_seconds)
    raise RuntimeError("Database initialization failed after retries") from last_error


def initialize_sqlite_tables(tables: list) -> None:
    # SQLite is used by the automated test suite; initialize it during import
    # because the tests construct TestClient without a lifespan context.
    if _is_sqlite:
        create_tables(tables)
