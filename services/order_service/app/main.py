import os
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field

app = FastAPI(title="Order Service", version="0.1.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL", "http://127.0.0.1:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003")
orders: dict[str, dict] = {}


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


@app.get("/health")
def health():
    return {"status": "ok", "service": "order-service"}


@app.post("/orders", status_code=201)
async def create_order(payload: OrderCreate, user: str = Depends(require_user), authorization: str | None = Header(default=None)):
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
        order = {
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
            order["payment"] = payment_response.json()
            order["status"] = "confirmed"
        except httpx.HTTPStatusError:
            order["status"] = "payment_failed"
            raise HTTPException(status_code=502, detail="Payment service rejected the request")
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Payment service unavailable")

    orders[order_id] = order
    return order


@app.get("/orders")
def list_orders(user: str = Depends(require_user)):
    return [order for order in orders.values() if order["user"] == user]


@app.get("/orders/{order_id}")
def get_order(order_id: str, user: str = Depends(require_user)):
    order = orders.get(order_id)
    if not order or order["user"] != user:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
