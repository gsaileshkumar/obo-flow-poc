"""Environment-driven configuration for poc-agent."""
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

        # poc-agent's own identity (confidential client: has a secret +
        # registered redirect URI).
        self.agent_client_id: str = _require("AGENT_APP_CLIENT_ID")
        self.agent_client_secret: str = _require("AGENT_APP_CLIENT_SECRET")

        # The scope poc-agent requests on login - an MCP-audience token,
        # since that's the only downstream service the agent talks to
        # directly. The MCP server then does OBO to reach poc-backend.
        self.mcp_client_id: str = _require("MCP_APP_CLIENT_ID")
        mcp_app_id_uri = os.environ.get(
            "MCP_APP_ID_URI", f"api://{self.mcp_client_id}"
        )
        self.mcp_scope: str = f"{mcp_app_id_uri}/{os.environ.get('MCP_REQUIRED_SCOPE', 'access_as_user')}"

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        self.redirect_uri: str = os.environ.get(
            "AGENT_REDIRECT_URI", "http://localhost:8501/auth/callback"
        )

        self.mcp_server_url: str = os.environ.get(
            "MCP_SERVER_URL", "http://127.0.0.1:8001/mcp"
        )

        self.host: str = os.environ.get("AGENT_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("AGENT_PORT", "8501"))

        # Cookie signing / session secret. Generate a real random value for
        # anything beyond local POC use.
        self.session_secret: str = os.environ.get(
            "AGENT_SESSION_SECRET", "dev-only-insecure-secret-change-me"
        )

        # Gemini model used by the ADK agent. Requires GOOGLE_API_KEY (or
        # Vertex AI application-default credentials) to be set - this is
        # independent of the Entra auth chain.
        self.adk_model: str = os.environ.get("ADK_MODEL", "gemini-2.0-flash")


settings = Settings()
