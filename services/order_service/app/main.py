import os
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.database import get_db, initialize_sqlite_tables
from services.order_service.app.models import Order

app = FastAPI(title="Order Service", version="0.3.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003")
initialize_sqlite_tables([Order.__table__])


class OrderCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return claims["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def serialize_order(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "user": order.user,
        "product_id": order.product_id,
        "product_name": order.product_name,
        "quantity": order.quantity,
        "total_amount": float(order.total_amount),
        "status": order.status,
        "payment": order.payment,
    }


@app.get("/health")
def health():
    return {"status": "ok", "service": "order-service"}


@app.post("/orders", status_code=201)
async def create_order(
    payload: OrderCreate,
    user: str = Depends(require_user),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    headers = {"Authorization": authorization} if authorization else {}
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            product_response = await client.get(
                f"{PRODUCT_SERVICE_URL}/products/{payload.product_id}", headers=headers
            )
            product_response.raise_for_status()
            product = product_response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=exc.response.status_code, detail="Unable to validate product")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Product service unavailable")

        order_id = str(uuid4())
        amount = round(product["price"] * payload.quantity, 2)
        order_data = {
            "order_id": order_id,
            "user": user,
            "product_id": product["id"],
            "product_name": product["name"],
            "quantity": payload.quantity,
            "total_amount": amount,
            "status": "payment_pending",
        }
        try:
            payment_response = await client.post(
                f"{PAYMENT_SERVICE_URL}/payments",
                json={"order_id": order_id, "amount": amount, "currency": "USD"},
            )
            payment_response.raise_for_status()
            payment = payment_response.json()
            order_data["payment"] = payment
            order_data["status"] = "confirmed"
        except httpx.HTTPStatusError:
            raise HTTPException(status_code=502, detail="Payment service rejected the request")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Payment service unavailable")

    order = Order(**order_data)
    db.add(order)
    db.commit()
    db.refresh(order)
    return serialize_order(order)


@app.get("/orders")
def list_orders(user: str = Depends(require_user), db: Session = Depends(get_db)):
    items = db.scalars(select(Order).where(Order.user == user).order_by(Order.created_at)).all()
    return [serialize_order(item) for item in items]


@app.get("/orders/{order_id}")
def get_order(order_id: str, user: str = Depends(require_user), db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order or order.user != user:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_order(order)
