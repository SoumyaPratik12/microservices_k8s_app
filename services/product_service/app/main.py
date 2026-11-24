import hashlib
import os
from datetime import datetime, timezone
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import get_db, initialize_sqlite_tables
from services.product_service.app.models import InventoryReservation, Product

app = FastAPI(title="Product Service", version="0.2.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
initialize_sqlite_tables([Product.__table__, InventoryReservation.__table__])


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    price: float = Field(gt=0)
    stock: int = Field(ge=0)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    price: float | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)


class StockReservation(BaseModel):
    quantity: int = Field(gt=0, le=100)


def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return claims["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def require_order_service(authorization: Annotated[str | None, Header()] = None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Order Service authorization required")
    try:
        claims = jwt.decode(
            authorization.split(" ", 1)[1],
            JWT_SECRET,
            algorithms=["HS256"],
            audience="product-service",
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid Order Service token")
    if claims.get("service") != "order-service":
        raise HTTPException(status_code=403, detail="Order Service authorization required")


def validate_reservation_key(key: str | None) -> str:
    if (
        not key
        or len(key) > 128
        or not all(char.isascii() and (char.isalnum() or char in "._:-") for char in key)
    ):
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    return key


def serialize_product(item: Product) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "price": float(item.price),
        "stock": item.stock,
        "created_by": item.created_by,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "product-service"}


@app.post("/products", status_code=201)
def create_product(payload: ProductCreate, user: str = Depends(require_user), db: Session = Depends(get_db)):
    item = Product(**payload.model_dump(), created_by=user)
    db.add(item)
    db.commit()
    db.refresh(item)
    return serialize_product(item)


@app.get("/products")
def list_products(user: str = Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Product).order_by(Product.id)).all()
    return [serialize_product(item) for item in items]


@app.get("/products/{product_id}")
def get_product(product_id: int, user: str = Depends(require_user), db: Session = Depends(get_db)):
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_product(item)


@app.get("/internal/products/{product_id}")
def get_product_for_order_service(
    product_id: int,
    _: None = Depends(require_order_service),
    db: Session = Depends(get_db),
):
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_product(item)


@app.post("/products/{product_id}/reserve", status_code=204)
def reserve_stock(
    product_id: int,
    payload: StockReservation,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    _: None = Depends(require_order_service),
    db: Session = Depends(get_db),
):
    reservation_key = validate_reservation_key(idempotency_key)
    request_hash = hashlib.sha256(f"{product_id}:{payload.quantity}".encode()).hexdigest()
    if db.get(Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")

    reservation = InventoryReservation(
        reservation_key=reservation_key,
        product_id=product_id,
        quantity=payload.quantity,
        request_hash=request_hash,
        status="PROCESSING",
    )
    db.add(reservation)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.get(InventoryReservation, reservation_key)
        if existing is None:
            raise HTTPException(status_code=503, detail="Reservation could not be resolved")
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409, detail="Idempotency-Key was already used for another reservation"
            )
        if existing.status == "RESERVED":
            return Response(status_code=204)
        if existing.status == "RELEASED":
            raise HTTPException(status_code=409, detail="This reservation was already released")
        raise HTTPException(status_code=409, detail="Insufficient product stock")

    result = cast(
        CursorResult,
        db.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock >= payload.quantity)
            .values(stock=Product.stock - payload.quantity)
        ),
    )
    if result.rowcount == 0:
        reservation.status = "FAILED"
        db.commit()
        raise HTTPException(status_code=409, detail="Insufficient product stock")
    reservation.status = "RESERVED"
    db.commit()
    return Response(status_code=204)


@app.post("/products/{product_id}/release", status_code=204)
def release_stock(
    product_id: int,
    payload: StockReservation,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    _: None = Depends(require_order_service),
    db: Session = Depends(get_db),
):
    reservation_key = validate_reservation_key(idempotency_key)
    request_hash = hashlib.sha256(f"{product_id}:{payload.quantity}".encode()).hexdigest()
    reservation = db.get(InventoryReservation, reservation_key)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    if reservation.request_hash != request_hash:
        raise HTTPException(
            status_code=409, detail="Idempotency-Key was already used for another reservation"
        )
    if reservation.status in {"RELEASED", "FAILED"}:
        return Response(status_code=204)

    now = datetime.now(timezone.utc)
    result = cast(
        CursorResult,
        db.execute(
            update(InventoryReservation)
            .where(
                InventoryReservation.reservation_key == reservation_key,
                InventoryReservation.status == "RESERVED",
            )
            .values(status="RELEASED", updated_at=now)
        ),
    )
    if result.rowcount == 0:
        db.rollback()
        existing = db.get(InventoryReservation, reservation_key)
        if existing and existing.status in {"RELEASED", "FAILED"}:
            return Response(status_code=204)
        raise HTTPException(status_code=409, detail="Reservation is not ready to release")
    result = cast(
        CursorResult,
        db.execute(
            update(Product).where(Product.id == product_id).values(stock=Product.stock + payload.quantity)
        ),
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=404, detail="Product not found")
    db.commit()
    return Response(status_code=204)


@app.patch("/products/{product_id}")
def update_product(
    product_id: int, payload: ProductUpdate, user: str = Depends(require_user), db: Session = Depends(get_db)
):
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return serialize_product(item)


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, user: str = Depends(require_user), db: Session = Depends(get_db)):
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(item)
    db.commit()
    return Response(status_code=204)
