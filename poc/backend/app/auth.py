"""Bearer-token validation for poc-backend.

This is the auth boundary of the whole POC chain: every call here must carry
an Entra-issued access token whose audience is THIS app (poc-backend) and
whose `scp` claim contains `access_as_user`. Anything else is rejected.

Validation is signature + iss + aud + exp, via PyJWT against Entra's JWKS,
exactly per the build spec (PyJWT + JWKS, no other token library).
"""
import logging

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import settings

logger = logging.getLogger("poc_backend.auth")

# PyJWKClient fetches and caches Entra's signing keys (https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys)
# and resolves the right key by `kid` in the token header, so we never hardcode a public key here.
_jwk_client = PyJWKClient(settings.jwks_uri)

# `auto_error=False` so we can return a clean 401 (instead of FastAPI's
# default 403) when the Authorization header is missing entirely.
_bearer_scheme = HTTPBearer(auto_error=False)


class AuthenticatedUser:
    """The caller's identity, extracted from validated token claims."""

    def __init__(self, claims: dict):
        self.oid: str | None = claims.get("oid")
        self.preferred_username: str | None = claims.get(
            "preferred_username"
        ) or claims.get("upn")
        self.scopes: list[str] = claims.get("scp", "").split()
        self.audience = claims.get("aud")
        self.raw_claims = claims


def _decode_and_validate(token: str) -> dict:
    """Validate signature, issuer, audience and expiry; return claims.

    Any failure raises 401 - we never distinguish "expired" vs "bad
    signature" vs "wrong issuer" in the response body, to avoid leaking
    validation internals to a caller, but we do log the reason server-side.
    """
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001 - any JWKS/format problem -> 401
        logger.warning("token rejected: could not resolve signing key (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: could not resolve signing key",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        claims = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            issuer=settings.issuer,
            audience=list(settings.allowed_audiences),
            leeway=settings.clock_skew_leeway_seconds,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        logger.warning("token rejected: expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad audience, bad issuer, bad signature, malformed claims.
        logger.warning("token rejected: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return claims


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """FastAPI dependency: validate the bearer token and enforce scope.

    Reusable across any endpoint that needs an authenticated, in-scope user:
    `def my_route(user: AuthenticatedUser = Depends(require_user))`.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = _decode_and_validate(credentials.credentials)
    user = AuthenticatedUser(claims)

    if settings.required_scope not in user.scopes:
        # Token is valid and for the right audience, but the user/client
        # never consented to (or wasn't granted) the access_as_user scope.
        logger.warning(
            "token rejected: missing required scope '%s' (oid=%s, scopes=%s)",
            settings.required_scope,
            user.oid,
            user.scopes,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token missing required scope '{settings.required_scope}'",
        )

    # Observability: prove the chain by logging which user/audience reached
    # us. Never log the token itself.
    logger.info("authenticated request: oid=%s aud=%s", user.oid, user.audience)

    return user
