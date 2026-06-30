"""Builds the ADK agent + MCP toolset for a single chat turn.

Per the build spec, the MCP connection is constructed per-user-request
(not as a shared module-level client), with the *current* user's poc-mcp
access token injected as the Authorization header on that connection. We
build a brand-new McpToolset (and therefore a brand-new MCP session) for
every turn and close it again afterwards - simplest possible way to
guarantee one user's token can never leak onto another user's connection,
at the cost of re-handshaking per turn. Fine for a POC; a production
version would likely cache one toolset per user-session instead and use
McpToolset's `header_provider` callback to swap tokens per-call.
"""
import logging

from google.adk.agents import Agent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import types

from config import settings

logger = logging.getLogger("poc_agent.mcp_agent")

APP_NAME = "poc_agent"

# One process-wide ADK session service is fine - it only holds *ADK*
# conversation turns (text history), never Entra tokens. Per-user isolation
# for those is via distinct (user_id, session_id) pairs, set up below.
session_service = InMemorySessionService()

AGENT_INSTRUCTION = """You are a small diagnostic assistant for the OBO (On-Behalf-Of) auth
proof-of-concept. You have exactly two tools, both backed by an MCP server
that authenticates as the current logged-in user:

- check_backend_health: use this when the user asks if the backend/service
  is up, running, healthy, or reachable.
- get_my_identity: use this when the user asks who they are, what their
  identity/oid/username is, or wants to confirm they're logged in as
  themselves.

Always call the appropriate tool rather than guessing. Report the tool's
JSON result back to the user in plain language."""


def _build_agent_and_toolset(mcp_access_token: str) -> tuple[Agent, McpToolset]:
    # Authorization header carries the CURRENT user's poc-mcp token. This is
    # what the MCP server's JWTVerifier validates and later uses as the OBO
    # user assertion - so getting this header right, per-user, per-request,
    # is the crux of the whole chain.
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=settings.mcp_server_url,
            headers={"Authorization": f"Bearer {mcp_access_token}"},
        ),
    )
    agent = Agent(
        name="poc_agent",
        model=settings.adk_model,
        description="Diagnostic assistant for the Entra OBO identity-chain POC.",
        instruction=AGENT_INSTRUCTION,
        tools=[toolset],
    )
    return agent, toolset


async def ensure_adk_session(user_id: str, session_id: str) -> None:
    existing = await session_service.get_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id
        )


async def run_turn(
    *, user_id: str, adk_session_id: str, mcp_access_token: str, message_text: str
) -> str:
    """Run one chat turn as `user_id`, calling MCP tools with the given
    poc-mcp access token, and return the agent's final text reply."""
    await ensure_adk_session(user_id, adk_session_id)

    agent, toolset = _build_agent_and_toolset(mcp_access_token)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)

    new_message = types.Content(role="user", parts=[types.Part(text=message_text)])
    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=user_id, session_id=adk_session_id, new_message=new_message
        ):
            if _is_final_text_event(event):
                final_text = _extract_text(event)
    finally:
        # Tear down this turn's MCP connection - never reused across users
        # or turns.
        await toolset.close()
        await runner.close()

    return final_text or "(no response)"


def _is_final_text_event(event: Event) -> bool:
    return event.is_final_response() and event.content is not None and event.content.parts


def _extract_text(event: Event) -> str:
    parts = event.content.parts or []
    return "".join(p.text or "" for p in parts if getattr(p, "text", None))
