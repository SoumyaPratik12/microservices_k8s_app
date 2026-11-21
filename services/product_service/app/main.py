import os
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, Field

app = FastAPI(title="Product Service", version="0.1.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
products: dict[int, dict] = {}
next_id = 1


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


@app.get("/health")
def health():
    return {"status": "ok", "service": "product-service"}


@app.post("/products", status_code=201)
def create_product(payload: ProductCreate, user: str = Depends(require_user)):
    global next_id
    item = {"id": next_id, **payload.model_dump(), "created_by": user}
    products[next_id] = item
    next_id += 1
    return item


@app.get("/products")
def list_products(user: str = Depends(require_user)):
    return list(products.values())


@app.get("/products/{product_id}")
def get_product(product_id: int, user: str = Depends(require_user)):
    item = products.get(product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.patch("/products/{product_id}")
def update_product(product_id: int, payload: ProductUpdate, user: str = Depends(require_user)):
    item = products.get(product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    item.update(payload.model_dump(exclude_unset=True))
    return item


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, user: str = Depends(require_user)):
    if product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")
    del products[product_id]
