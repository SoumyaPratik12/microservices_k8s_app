import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, status
from jose import jwt
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="Auth Service", version="0.1.0")
JWT_SECRET = os.getenv("JWT_SECRET", "development-only-change-me")
users: dict[str, dict[str, str]] = {}


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
def register(payload: RegisterRequest):
    email = payload.email.lower()
    if email in users:
        raise HTTPException(status_code=409, detail="Email already registered")
    salt, password_hash = hash_password(payload.password)
    users[email] = {"salt": salt, "password_hash": password_hash}
    return {"email": email, "message": "User registered"}


@app.post("/login")
def login(payload: LoginRequest):
    email = payload.email.lower()
    user = users.get(email)
    if not user or not verify_password(payload.password, user["salt"], user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": email, "iat": now, "exp": now + timedelta(minutes=60)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}
