import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from services.auth_service.app.models import User
from services.order_service.app.models import Order
from services.product_service.app.models import Product


def test_upgrade_adopts_existing_tables_and_preserves_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)

    with engine.begin() as connection:
        User.__table__.create(connection)
        Product.__table__.create(connection)
        Order.__table__.create(connection)
        connection.execute(
            User.__table__.insert().values(
                email="legacy@example.com",
                salt="salt",
                password_hash="hash",
                created_at=now,
            )
        )
        connection.execute(
            Product.__table__.insert().values(
                id=1,
                name="Legacy product",
                description="Existing product",
                price=2.50,
                stock=5,
                created_by="legacy@example.com",
                created_at=now,
            )
        )
        connection.execute(
            Order.__table__.insert().values(
                order_id="legacy-order",
                user="legacy@example.com",
                product_id=1,
                product_name="Legacy product",
                quantity=1,
                total_amount=2.50,
                status="confirmed",
                payment={"status": "succeeded"},
                created_at=now,
            )
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )

    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM auth_users")) == 1
        assert connection.scalar(text("SELECT count(*) FROM catalog_products")) == 1
        assert connection.scalar(text("SELECT count(*) FROM customer_orders")) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "3c90a7b41d62"
    assert {"idempotency_records", "payment_ledger", "inventory_reservations"} <= set(
        inspect(engine).get_table_names()
    )
