import json
from functools import lru_cache
from typing import Any
from urllib.request import urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwk, jwt

from app.core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def _jwt_secret() -> str:
    secret = settings.JWT_SECRET_KEY or settings.SUPABASE_JWT_SECRET
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret is not configured on the server",
        )
    return secret


@lru_cache(maxsize=4)
def _supabase_signing_key(kid: str) -> bytes:
    if not settings.SUPABASE_URL:
        raise JWTError("Supabase URL is not configured")
    with urlopen(  # noqa: S310 - URL is the operator-configured Supabase origin.
        f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json",
        timeout=5,
    ) as response:
        keys = json.load(response).get("keys", [])
    match = next((key for key in keys if key.get("kid") == kid), None)
    if match is None:
        raise JWTError("Supabase signing key was not found")
    return jwk.construct(match, algorithm=match["alg"]).to_pem()


def decode_token(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "ES256":
            payload = jwt.decode(
                token,
                _supabase_signing_key(header.get("kid", "")),
                algorithms=["ES256"],
                audience="authenticated",
            )
        else:
            payload = jwt.decode(token, _jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
        return payload
    except (JWTError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> dict[str, Any]:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing subject",
        )

    return {
        "id": user_id,
        "email": payload.get("email"),
        "role": payload.get("role", "researcher"),
        "claims": payload,
    }
