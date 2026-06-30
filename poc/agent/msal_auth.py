"""MSAL auth-code-flow login + silent refresh for poc-agent.

poc-agent is a confidential client (has its own secret) even though a human
logs in interactively, because it needs to redeem the authorization code
server-side and, later, silently refresh tokens without bothering the user.

Every function here builds a fresh ConfidentialClientApplication backed by
a SerializableTokenCache loaded from (and saved back to) the caller's own
session - never a module-level/shared cache - so tokens for different users
never mix. This is the standard "stateless" MSAL web-app pattern.
"""
import logging

import msal

from config import settings

logger = logging.getLogger("poc_agent.msal_auth")


class LoginRequiredError(Exception):
    """Raised when we cannot silently produce a valid MCP-audience token
    and the user must be sent through interactive login again (e.g. the
    refresh token itself expired or was revoked)."""


def _new_cca(serialized_cache: str | None = None) -> tuple[msal.ConfidentialClientApplication, msal.SerializableTokenCache]:
    cache = msal.SerializableTokenCache()
    if serialized_cache:
        cache.deserialize(serialized_cache)
    cca = msal.ConfidentialClientApplication(
        client_id=settings.agent_client_id,
        client_credential=settings.agent_client_secret,
        authority=settings.authority,
        token_cache=cache,
    )
    return cca, cache


def start_login() -> dict:
    """Begin the auth-code flow. Returns the MSAL `flow` dict, which must
    be persisted (server-side, keyed by a temporary cookie) until the
    callback arrives - it contains the PKCE verifier and expected state.
    """
    cca, _ = _new_cca()
    flow = cca.initiate_auth_code_flow(
        scopes=[settings.mcp_scope],
        redirect_uri=settings.redirect_uri,
    )
    return flow


def complete_login(flow: dict, callback_params: dict) -> dict:
    """Redeem the authorization code from the callback.

    Returns a dict with: oid, preferred_username, access_token,
    msal_cache (serialized, to store in the new user session).
    """
    cca, cache = _new_cca()
    result = cca.acquire_token_by_auth_code_flow(flow, callback_params)

    if "access_token" not in result:
        error = result.get("error", "unknown_error")
        description = result.get("error_description", "")
        logger.warning("login failed: error=%s", error)
        raise LoginRequiredError(f"Login failed ({error}): {description}")

    claims = result.get("id_token_claims", {})
    return {
        "oid": claims.get("oid"),
        "preferred_username": claims.get("preferred_username") or claims.get("upn"),
        "access_token": result["access_token"],
        "msal_cache": cache.serialize(),
    }


def get_valid_mcp_token(serialized_cache: str) -> tuple[str, str]:
    """Silently refresh (if needed) and return a valid poc-mcp-audience
    access token, plus the (possibly updated) serialized cache to persist
    back into the user's session.

    Raises LoginRequiredError if silent refresh isn't possible, so the
    caller can send the user back through /auth/login instead of breaking
    mid-conversation with an opaque error.
    """
    cca, cache = _new_cca(serialized_cache)
    accounts = cca.get_accounts()
    if not accounts:
        raise LoginRequiredError("No cached account; interactive login required")

    result = cca.acquire_token_silent(scopes=[settings.mcp_scope], account=accounts[0])
    if not result or "access_token" not in result:
        logger.info("silent token refresh failed; interactive login required")
        raise LoginRequiredError("Silent token refresh failed; interactive login required")

    new_cache = cache.serialize() if cache.has_state_changed else serialized_cache
    return result["access_token"], new_cache
