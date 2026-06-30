"""Environment-driven configuration for poc-backend.

Every value that identifies the tenant or this app's identity comes from the
environment so the same code works for any Entra tenant / app registration
without edits.
"""
import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


class Settings:
    """Loaded once at process start. Read from environment / .env."""

    def __init__(self) -> None:
        # Entra tenant that issues the tokens we accept.
        self.tenant_id: str = _require("ENTRA_TENANT_ID")

        # This app's own Application (client) ID. The access token's `aud`
        # claim must equal `api://<backend_client_id>` (or just the GUID,
        # depending on how the App ID URI was configured) for a request to
        # be accepted as ours.
        self.backend_client_id: str = _require("BACKEND_APP_CLIENT_ID")

        # Accept either the App ID URI form or the bare client id as a valid
        # audience, since Entra tokens can be minted either way depending on
        # how the exposed API's Application ID URI is set.
        app_id_uri = os.environ.get(
            "BACKEND_APP_ID_URI", f"api://{self.backend_client_id}"
        )
        self.allowed_audiences = {app_id_uri, self.backend_client_id}

        # The delegated scope this API exposes. Tokens must carry this in
        # the `scp` claim.
        self.required_scope: str = os.environ.get(
            "BACKEND_REQUIRED_SCOPE", "access_as_user"
        )

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.issuer: str = os.environ.get(
            "ENTRA_ISSUER", f"{self.authority}/v2.0"
        )
        self.jwks_uri: str = os.environ.get(
            "ENTRA_JWKS_URI",
            f"{self.authority}/discovery/v2.0/keys",
        )

        # Allow a small clock-skew leeway (seconds) when checking exp/nbf,
        # since the backend, MCP server and Entra are on different clocks.
        self.clock_skew_leeway_seconds: int = int(
            os.environ.get("CLOCK_SKEW_LEEWAY_SECONDS", "60")
        )

        self.host: str = os.environ.get("BACKEND_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("BACKEND_PORT", "8000"))


settings = Settings()
