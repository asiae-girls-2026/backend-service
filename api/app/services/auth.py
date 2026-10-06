"""무작위 Bearer 토큰 인증. DB에는 SHA-256 해시만 저장."""
import hashlib
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    cp_number: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not db.available():
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        with db.conn() as c:
            row = c.execute(
                """SELECT user_id, cp_number
                   FROM users WHERE token_hash = %s""",
                (token_hash(credentials.credentials),),
            ).fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="Authentication unavailable")

    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthUser(user_id=str(row[0]), cp_number=row[1])
