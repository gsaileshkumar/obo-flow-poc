"""poc-agent: the user-facing entry point of the OBO chain.

Wraps a Google ADK agent in a small FastAPI app that:
  1. Gates the chat UI behind Entra login (MSAL auth-code flow).
  2. Keeps every user's tokens in their own server-side session (cookie =
     opaque session id only, never a token).
  3. On each chat turn, silently refreshes (or re-prompts for) a poc-mcp
     access token and hands it to the ADK agent's MCP toolset for that
     request only.

Deviation note: the build spec says "ADK's built-in web UI is fine", but
that UI is a static CLI-launched server with no hook point for per-user
Entra login or per-request Authorization headers on the MCP connection.
Since both are required by this spec, we instead drive the ADK Runner
programmatically from this thin FastAPI app and serve a minimal bespoke
chat page. The ADK agent definition itself (Agent + McpToolset + Runner)
is unchanged from what `adk web` would use.
"""
import logging
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import session_store
from config import settings
from mcp_agent import run_turn
from msal_auth import LoginRequiredError, complete_login, get_valid_mcp_token, start_login

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("poc_agent")

app = FastAPI(title="poc-agent")

SESSION_COOKIE = "poc_agent_session"
PENDING_LOGIN_COOKIE = "poc_agent_pending_login"


def _current_session(request: Request) -> session_store.UserSession | None:
    return session_store.get_session(request.cookies.get(SESSION_COOKIE))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    user = _current_session(request)
    if user is None:
        return HTMLResponse(_LOGIN_PAGE)
    return HTMLResponse(_chat_page(user.preferred_username))


@app.get("/auth/login")
async def login() -> RedirectResponse:
    """Step 1 of the auth-code flow: redirect the browser to Entra.

    The flow dict (PKCE verifier + expected state) is stashed server-side
    under a short-lived, single-use pending-login id, referenced by a
    temporary cookie - it must survive the round trip to Entra and back.
    """
    flow = start_login()
    pending_id = session_store.create_pending_login(flow)

    response = RedirectResponse(url=flow["auth_uri"])
    response.set_cookie(
        PENDING_LOGIN_COOKIE,
        pending_id,
        httponly=True,
        samesite="lax",
        max_age=600,
    )
    return response


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    """Step 2: Entra redirects back here with `code` and `state`.

    We redeem the code for tokens scoped to poc-mcp (api://<mcp-app-id>/access_as_user),
    establish a new per-user session, and never expose the tokens to the
    browser - only an opaque session cookie.
    """
    pending_id = request.cookies.get(PENDING_LOGIN_COOKIE)
    flow = session_store.pop_pending_login(pending_id) if pending_id else None
    if flow is None:
        return RedirectResponse(url="/?error=login_session_expired")

    try:
        result = complete_login(flow, dict(request.query_params))
    except LoginRequiredError as exc:
        logger.warning("login callback failed: %s", exc)
        return RedirectResponse(url="/?error=login_failed")

    session = session_store.create_session(
        oid=result["oid"],
        preferred_username=result["preferred_username"] or result["oid"],
        msal_cache=result["msal_cache"],
    )
    logger.info("user logged in: oid=%s", session.oid)

    response = RedirectResponse(url="/")
    response.delete_cookie(PENDING_LOGIN_COOKIE)
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        httponly=True,
        samesite="lax",
        # No max_age: a browser-session cookie. The MSAL refresh token in
        # the server-side cache is what actually controls how long the
        # user stays signed in.
    )
    return response


@app.post("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        session_store.delete_session(session_id)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.post("/chat")
async def chat(request: Request) -> JSONResponse:
    user = _current_session(request)
    if user is None:
        return JSONResponse({"error": "not_logged_in"}, status_code=401)

    body = await request.json()
    message_text = (body.get("message") or "").strip()
    if not message_text:
        return JSONResponse({"error": "empty_message"}, status_code=400)

    # Silent refresh before every MCP call, so a long-running conversation
    # never breaks mid-stream just because the access token's ~1hr lifetime
    # elapsed. If even the refresh token is no longer usable, send the user
    # back through interactive login instead of failing the chat turn
    # silently.
    try:
        mcp_token, refreshed_cache = get_valid_mcp_token(user.msal_cache)
    except LoginRequiredError:
        return JSONResponse({"error": "login_required", "login_url": "/auth/login"}, status_code=401)

    session_store.update_msal_cache(user.session_id, refreshed_cache)

    if user.adk_session_id is None:
        session_store.set_adk_session_id(user.session_id, secrets.token_urlsafe(16))
        user = _current_session(request)

    reply = await run_turn(
        user_id=user.oid,
        adk_session_id=user.adk_session_id,
        mcp_access_token=mcp_token,
        message_text=message_text,
    )
    return JSONResponse({"reply": reply})


_LOGIN_PAGE = """<!DOCTYPE html>
<html><head><title>poc-agent</title></head>
<body style="font-family: sans-serif; max-width: 480px; margin: 80px auto; text-align: center;">
  <h1>OBO Flow POC</h1>
  <p>Sign in with your Entra ID account to chat with the agent.</p>
  <a href="/auth/login" style="display:inline-block;padding:10px 20px;background:#0078d4;color:white;text-decoration:none;border-radius:4px;">
    Sign in with Microsoft
  </a>
</body></html>"""


def _chat_page(username: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><title>poc-agent</title></head>
<body style="font-family: sans-serif; max-width: 720px; margin: 40px auto;">
  <div style="display:flex; justify-content:space-between; align-items:center;">
    <h2>poc-agent</h2>
    <div>
      <span>{username}</span>
      <form method="post" action="/auth/logout" style="display:inline;">
        <button type="submit">Sign out</button>
      </form>
    </div>
  </div>
  <div id="log" style="border:1px solid #ccc; border-radius:4px; min-height:300px; padding:12px; white-space:pre-wrap;"></div>
  <form id="form" style="display:flex; gap:8px; margin-top:12px;">
    <input id="input" autocomplete="off" placeholder="Ask: is the backend up? / who am I?" style="flex:1; padding:8px;" />
    <button type="submit">Send</button>
  </form>
  <script>
    const log = document.getElementById('log');
    const form = document.getElementById('form');
    const input = document.getElementById('input');
    function append(who, text) {{
      const p = document.createElement('div');
      p.textContent = who + ': ' + text;
      log.appendChild(p);
      log.scrollTop = log.scrollHeight;
    }}
    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      const message = input.value.trim();
      if (!message) return;
      append('you', message);
      input.value = '';
      const res = await fetch('/chat', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{message}}),
      }});
      if (res.status === 401) {{
        const data = await res.json();
        if (data.login_url) {{ window.location.href = data.login_url; return; }}
      }}
      const data = await res.json();
      append('agent', data.reply || data.error || 'error');
    }});
  </script>
</body></html>"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=settings.host, port=settings.port)
