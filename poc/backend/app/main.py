"""poc-backend: the innermost service in the OBO chain.

Exposes one public, unauthenticated endpoint and one private endpoint that
requires a valid Entra access token with aud=poc-backend and
scp containing access_as_user. This is the service that proves OBO worked:
if you can reach /private/whoami with the *user's* identity, the agent ->
MCP -> OBO -> backend chain is intact.
"""
import logging

from fastapi import Depends, FastAPI

from app.auth import AuthenticatedUser, require_user
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("poc_backend")

app = FastAPI(title="poc-backend")


@app.get("/public/health")
async def health() -> dict:
    """No auth required. Used by the MCP server's check_backend_health tool."""
    return {"status": "ok", "service": "poc-backend"}


@app.get("/private/whoami")
async def whoami(user: AuthenticatedUser = Depends(require_user)) -> dict:
    """Requires a valid bearer token (aud=poc-backend, scp=access_as_user).

    Returns the identity extracted from the validated token's claims - this
    is "running as the user" because the only way to land here with a given
    oid is to present a token Entra minted for that user.
    """
    return {
        "oid": user.oid,
        "preferred_username": user.preferred_username,
        "scopes": user.scopes,
        "message": f"you reached the private endpoint as {user.preferred_username or user.oid}",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
