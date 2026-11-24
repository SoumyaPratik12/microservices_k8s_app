import os
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def pytest_configure(config):
    mark_expression = (config.getoption("markexpr") or "").strip()
    if mark_expression != "postgresql":
        test_directory = tempfile.mkdtemp(prefix="microservices-unit-tests-")
        config._microservices_test_directory = test_directory
        os.environ["DATABASE_URL"] = f"sqlite:///{test_directory}/unit-tests.db"
        return

    test_url = os.getenv("TEST_POSTGRES_URL")
    if not test_url:
        raise pytest.UsageError(
            "PostgreSQL tests require TEST_POSTGRES_URL; see scripts/run-postgres-tests.sh"
        )
    parsed_url = make_url(test_url)
    if parsed_url.database != "test_microservices":
        raise pytest.UsageError("Refusing PostgreSQL tests unless the database is named test_microservices")

    schema = f"test_{uuid.uuid4().hex}"
    admin_engine = create_engine(parsed_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    isolated_url = parsed_url.set(query={**parsed_url.query, "options": f"-csearch_path={schema}"})
    os.environ["DATABASE_URL"] = isolated_url.render_as_string(hide_password=False)
    config._microservices_postgres_admin_engine = admin_engine
    config._microservices_postgres_schema = schema


def pytest_sessionstart(session):
    if getattr(session.config, "_microservices_postgres_schema", None):
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            env=os.environ.copy(),
        )


def pytest_sessionfinish(session, exitstatus):
    schema = getattr(session.config, "_microservices_postgres_schema", None)
    admin_engine = getattr(session.config, "_microservices_postgres_admin_engine", None)
    if schema and admin_engine:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def pytest_unconfigure(config):
    test_directory = getattr(config, "_microservices_test_directory", None)
    if test_directory:
        shutil.rmtree(test_directory, ignore_errors=True)
