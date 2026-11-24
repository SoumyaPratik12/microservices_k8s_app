import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, status
import jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import get_db, initialize_sqlite_tables
from services.auth_service.app.models import User

app = FastAPI(title="Auth Service", version="0.2.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me-please-use-a-strong-secret")
initialize_sqlite_tables([User.__table__])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    _, actual = hash_password(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(actual, expected_hex)


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-service"}


@app.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.get(User, email):
        raise HTTPException(status_code=409, detail="Email already registered")
    salt, password_hash = hash_password(payload.password)
    user = User(email=email, salt=salt, password_hash=password_hash)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered")
    return {"email": email, "message": "User registered"}


@app.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.lower()
    user = db.get(User, email)
    if not user or not verify_password(payload.password, user.salt, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": email, "iat": now, "exp": now + timedelta(minutes=60)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}
