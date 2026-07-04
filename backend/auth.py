"""
auth.py
--------
Password hashing, JWT session tokens, and the RBAC dependency
factory (`require_role`) used to protect FastAPI routes.

RBAC hierarchy (highest -> lowest):
    super_admin > admin > employee > client
"""

from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

# --------------------------------------------------------------------------
# Config (in production, load SECRET_KEY from an environment variable /
# secrets manager, never hardcode it).
# --------------------------------------------------------------------------
SECRET_KEY = "REPLACE_WITH_A_LONG_RANDOM_SECRET_IN_PRODUCTION"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 hour session

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ROLE_RANK = {
    "client": 0,
    "employee": 1,
    "admin": 2,
    "super_admin": 3,
}


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Decodes the bearer token and returns the identity claims embedded in it.
    This is injected into every protected route so handlers always know
    exactly *who* is calling and *what role* they hold.
    """
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    role = payload.get("role")
    status_claim = payload.get("status")

    if user_id is None or role is None:
        raise HTTPException(status_code=401, detail="Malformed session token")

    if status_claim == "suspended":
        raise HTTPException(status_code=403, detail="This account has been suspended")

    return {
        "id": int(user_id),
        "role": role,
        "email": payload.get("email"),
        "name": payload.get("name"),
        "company_name": payload.get("company_name"),
        "is_independent": bool(payload.get("is_independent", False)),
    }


def require_role(allowed_roles: Iterable[str]):
    """
    Dependency factory: require_role(["admin", "super_admin"])
    Raises 403 if current_user's role is not in the allowed set.
    """
    allowed = set(allowed_roles)

    async def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user['role']}' is not permitted to access this resource",
            )
        return current_user

    return _dependency


def require_min_rank(minimum_role: str):
    """
    Dependency factory based on hierarchy rank rather than an explicit list.
    e.g. require_min_rank("employee") allows employee, admin, super_admin.
    """
    min_rank = ROLE_RANK[minimum_role]

    async def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK.get(current_user["role"], -1) < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient privileges for this resource",
            )
        return current_user

    return _dependency
