"""Per-user session storage for poc-agent.

Deliberately NOT a single process-wide token: every browser session gets its
own opaque session id (random, set as an HttpOnly cookie) which maps to its
own MSAL token cache, account info, and ADK conversation session id. Two
users hitting this same process never see each other's tokens.

This is an in-memory dict for POC simplicity - restarting the agent process
logs everyone out. A real deployment would swap this for Redis or a DB
without changing the call sites below.
"""
import secrets
import time
from dataclasses import dataclass, field


@dataclass
class UserSession:
    session_id: str
    oid: str
    preferred_username: str
    # MSAL SerializableTokenCache.serialize() output for this user only.
    msal_cache: str
    # ADK conversation session id, created lazily on first chat turn.
    adk_session_id: str | None = None
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, UserSession] = {}

# Short-lived storage for in-flight MSAL auth-code-flow state, between
# /auth/login (which generates the flow) and /auth/callback (which
# completes it). Keyed by a separate opaque id from a temporary cookie -
# never the final session id.
_pending_logins: dict[str, dict] = {}
_PENDING_LOGIN_TTL_SECONDS = 600


def create_pending_login(flow: dict) -> str:
    pending_id = secrets.token_urlsafe(32)
    _pending_logins[pending_id] = {"flow": flow, "created_at": time.time()}
    _prune_pending_logins()
    return pending_id


def pop_pending_login(pending_id: str) -> dict | None:
    entry = _pending_logins.pop(pending_id, None)
    if entry is None:
        return None
    return entry["flow"]


def _prune_pending_logins() -> None:
    cutoff = time.time() - _PENDING_LOGIN_TTL_SECONDS
    expired = [pid for pid, e in _pending_logins.items() if e["created_at"] < cutoff]
    for pid in expired:
        _pending_logins.pop(pid, None)


def create_session(oid: str, preferred_username: str, msal_cache: str) -> UserSession:
    session_id = secrets.token_urlsafe(32)
    session = UserSession(
        session_id=session_id,
        oid=oid,
        preferred_username=preferred_username,
        msal_cache=msal_cache,
    )
    _sessions[session_id] = session
    return session


def get_session(session_id: str | None) -> UserSession | None:
    if not session_id:
        return None
    return _sessions.get(session_id)


def update_msal_cache(session_id: str, msal_cache: str) -> None:
    session = _sessions.get(session_id)
    if session is not None:
        session.msal_cache = msal_cache


def set_adk_session_id(session_id: str, adk_session_id: str) -> None:
    session = _sessions.get(session_id)
    if session is not None:
        session.adk_session_id = adk_session_id


def delete_session(session_id: str) -> None:
    _sessions.pop(session_id, None)
