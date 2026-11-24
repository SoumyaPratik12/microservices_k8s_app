from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Payment Service", version="0.3.0")


class PaymentRequest(BaseModel):
    order_id: str
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)


@app.get("/health")
def health():
    return {"status": "ok", "service": "payment-service"}


@app.post("/payments", status_code=201)
def process_payment(payload: PaymentRequest):
    # Simulation only: no real payment gateway or money movement.
    return {
        "payment_id": str(uuid4()),
        "order_id": payload.order_id,
        "amount": payload.amount,
        "currency": payload.currency.upper(),
        "status": "succeeded",
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "simulation": True,
    }
