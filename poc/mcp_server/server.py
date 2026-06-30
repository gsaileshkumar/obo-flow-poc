"""poc-mcp: the middle hop in the OBO chain.

Validates that the caller (the agent, on behalf of a logged-in user) sent a
token for *this* app (aud=poc-mcp), then exposes two tools that call
poc-backend - one public, one OBO'd as the user.

FastMCP auth surface used here
-------------------------------
FastMCP (>=2.x) has a built-in resource-server auth layer: passing
`auth=JWTVerifier(...)` to `FastMCP(...)` makes the framework itself
validate the inbound `Authorization: Bearer <jwt>` header (signature via
JWKS, iss, aud, exp, and required scopes) on *every* request before any
tool code runs, and reject non-conforming requests with 401 automatically.
This is the FastMCP-idiomatic equivalent of the backend's hand-rolled
PyJWT `require_user` dependency, so we use it here instead of re-deriving
the same logic - this is the documented "FastMCP API differs from a literal
PyJWT dependency, adapted" deviation called out in the build spec.

To read the validated token's claims (and the raw JWT, needed as the OBO
"user assertion") from inside a tool, FastMCP exposes
`fastmcp.server.dependencies.get_access_token()`, which returns the
`AccessToken` produced by the JWTVerifier above for the current request.
If a different FastMCP version exposes this differently, that function is
the one seam to adapt.
"""
import logging

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

from config import settings
from obo import OboError, acquire_backend_token_on_behalf_of

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("poc_mcp")

# --- Auth boundary -----------------------------------------------------
# Every MCP request must carry a bearer token minted for poc-mcp
# (aud=api://<mcp-app-id>) with the access_as_user delegated scope. This
# mirrors the backend's validation (same JWKS/issuer/exp checks) but for a
# different audience - proving the agent cannot reuse a poc-backend token
# here, and vice versa (confused-deputy protection).
auth_provider = JWTVerifier(
    jwks_uri=settings.jwks_uri,
    issuer=settings.issuer,
    audience=settings.allowed_audiences,
    required_scopes=[settings.required_scope],
    base_url=settings.base_url,
)

mcp = FastMCP(name="poc-mcp", auth=auth_provider)


def _current_oid() -> str | None:
    """Pull the validated caller's oid out of the current request's token.

    Available because JWTVerifier already validated the token before this
    tool body runs; `get_access_token()` just reads the result back out of
    request-scoped context.
    """
    access_token = get_access_token()
    if access_token is None:
        return None
    return (access_token.claims or {}).get("oid")


@mcp.tool
async def check_backend_health() -> dict:
    """Check whether poc-backend is reachable and healthy.

    Use this when the user asks things like "is the backend up?",
    "check backend health", or "is the service running?".
    Calls poc-backend's public, unauthenticated /public/health endpoint.
    """
    oid = _current_oid()
    # The endpoint itself needs no token, but the MCP *call* still required
    # a valid poc-mcp token to get here (enforced by JWTVerifier above) -
    # logged for consistency/observability even though this path is public.
    logger.info("tool=check_backend_health oid=%s downstream_aud=none(public)", oid)

    async with httpx.AsyncClient(base_url=settings.backend_base_url, timeout=10) as client:
        response = await client.get("/public/health")
        response.raise_for_status()
        return response.json()


@mcp.tool
async def get_my_identity() -> dict:
    """Return the caller's own identity (oid, username) as known by poc-backend.

    Use this when the user asks "who am I?", "what's my identity?", or
    "what user am I logged in as?". This performs an On-Behalf-Of token
    exchange so the call to poc-backend is made as the logged-in user, not
    as the MCP server itself.
    """
    access_token = get_access_token()
    if access_token is None:
        # Should be unreachable - JWTVerifier rejects unauthenticated
        # requests before a tool can run - but fail loudly if it happens.
        raise RuntimeError("No validated access token in request context")

    oid = (access_token.claims or {}).get("oid")
    logger.info(
        "tool=get_my_identity oid=%s obo_target_aud=%s",
        oid,
        settings.backend_client_id,
    )

    try:
        # Exchange the inbound poc-mcp token for a poc-backend token, still
        # bound to this same user (oid). Never log either token.
        backend_token = acquire_backend_token_on_behalf_of(access_token.token)
    except OboError as exc:
        logger.warning("get_my_identity: OBO exchange failed for oid=%s", oid)
        return {"error": str(exc)}

    async with httpx.AsyncClient(base_url=settings.backend_base_url, timeout=10) as client:
        response = await client.get(
            "/private/whoami",
            headers={"Authorization": f"Bearer {backend_token}"},
        )
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    # Private network assumption: bind to localhost only, never 0.0.0.0.
    mcp.run(transport="streamable-http", host=settings.host, port=settings.port)
