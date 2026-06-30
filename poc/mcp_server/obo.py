"""On-Behalf-Of token exchange: trade the caller's poc-mcp token for a
poc-backend token, without ever asking the user to log in again.

This is the heart of the delegated-identity chain: MSAL's
acquire_token_on_behalf_of takes the *inbound* user access token (the
"user assertion") and, using poc-mcp's own client credential, asks Entra
for a *new* access token for poc-backend that still represents the same
user. The user's identity (oid) is preserved across the hop; only the
audience changes.
"""
import logging

import msal

from config import settings

logger = logging.getLogger("poc_mcp.obo")

# A single ConfidentialClientApplication for the process is the documented
# MSAL pattern for OBO: it holds poc-mcp's own client credential, and its
# default in-memory token cache is keyed (internally, by MSAL) per distinct
# user assertion / downstream scope, so concurrent users do not share or
# collide on each other's tokens even though the app instance is shared.
# We deliberately rely on this built-in cache instead of writing our own.
#
# Built lazily (on first OBO call, not at import time) because MSAL
# performs a network call (OIDC tenant discovery) when constructing a
# ConfidentialClientApplication, and we don't want server startup to
# require network access / a valid tenant before it can even serve
# check_backend_health.
_cca: msal.ConfidentialClientApplication | None = None


def _get_cca() -> msal.ConfidentialClientApplication:
    global _cca
    if _cca is None:
        _cca = msal.ConfidentialClientApplication(
            client_id=settings.mcp_client_id,
            client_credential=settings.mcp_client_secret,
            authority=settings.authority,
        )
    return _cca


class OboError(Exception):
    """Raised when Entra refuses the OBO exchange.

    Most common causes (see README troubleshooting section):
      - invalid_grant: poc-mcp is missing the delegated permission to
        poc-backend's access_as_user scope, or admin consent wasn't granted.
      - invalid_client: wrong/expired MCP_APP_CLIENT_SECRET.
    """


def acquire_backend_token_on_behalf_of(user_assertion: str) -> str:
    """Exchange the inbound user token for a poc-backend-audience token.

    `user_assertion` is the raw JWT the agent sent us (aud=poc-mcp). We
    never log it or the resulting token - only the fact that an exchange
    happened and for which user/audience.
    """
    result = _get_cca().acquire_token_on_behalf_of(
        user_assertion=user_assertion,
        scopes=[settings.backend_obo_scope],
    )

    if "access_token" not in result:
        error = result.get("error", "unknown_error")
        description = result.get("error_description", "")
        logger.warning("OBO exchange failed: error=%s", error)
        raise OboError(f"OBO exchange failed ({error}): {description}")

    return result["access_token"]
