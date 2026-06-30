"""Environment-driven configuration for poc-mcp."""
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
    def __init__(self) -> None:
        self.tenant_id: str = _require("ENTRA_TENANT_ID")

        # poc-mcp's own identity. Inbound tokens (from the agent) must have
        # aud == api://<mcp_client_id> (or the bare id).
        self.mcp_client_id: str = _require("MCP_APP_CLIENT_ID")
        self.mcp_client_secret: str = _require("MCP_APP_CLIENT_SECRET")

        mcp_app_id_uri = os.environ.get(
            "MCP_APP_ID_URI", f"api://{self.mcp_client_id}"
        )
        self.allowed_audiences = [mcp_app_id_uri, self.mcp_client_id]
        self.required_scope: str = os.environ.get(
            "MCP_REQUIRED_SCOPE", "access_as_user"
        )

        # The downstream app (poc-backend) we perform OBO against.
        self.backend_client_id: str = _require("BACKEND_APP_CLIENT_ID")
        backend_app_id_uri = os.environ.get(
            "BACKEND_APP_ID_URI", f"api://{self.backend_client_id}"
        )
        self.backend_obo_scope: str = f"{backend_app_id_uri}/{os.environ.get('BACKEND_REQUIRED_SCOPE', 'access_as_user')}"
        self.backend_base_url: str = os.environ.get(
            "BACKEND_BASE_URL", "http://127.0.0.1:8000"
        )

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.issuer: str = os.environ.get("ENTRA_ISSUER", f"{self.authority}/v2.0")
        self.jwks_uri: str = os.environ.get(
            "ENTRA_JWKS_URI", f"{self.authority}/discovery/v2.0/keys"
        )

        self.host: str = os.environ.get("MCP_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("MCP_PORT", "8001"))
        # Public URL the agent uses to reach this server (for the
        # FastMCP auth provider's resource metadata).
        self.base_url: str = os.environ.get(
            "MCP_BASE_URL", f"http://{self.host}:{self.port}"
        )


settings = Settings()
