import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.database import get_db, initialize_sqlite_tables
from services.product_service.app.models import Product

app = FastAPI(title="Product Service", version="0.2.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
initialize_sqlite_tables([Product.__table__])


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


def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        claims = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"])
        return claims["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


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


@app.patch("/products/{product_id}")
def update_product(product_id: int, payload: ProductUpdate, user: str = Depends(require_user), db: Session = Depends(get_db)):
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
