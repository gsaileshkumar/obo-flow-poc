# Entra ID → MCP → ADK Agent: End-to-End OBO Identity Chain (POC)

A proof-of-concept showing a full delegated-identity chain: a user logs
into an agent chat UI with Entra ID, asks a question, the LLM routes to an
MCP tool, and the call reaches a backend **as that user** via OAuth
On-Behalf-Of (OBO) - never as a shared service identity.

```
User → poc-agent (auth code login, token aud=poc-mcp)
     → poc-mcp (validates aud=poc-mcp) → OBO exchange → token aud=poc-backend
        → poc-backend (validates aud=poc-backend, runs as the user)
```

Business logic is intentionally trivial (a health check and a whoami). The
point of this repo is the auth chain, not the app.

## Components

| Component | Path | Stack |
|---|---|---|
| Backend | `backend/` | FastAPI, PyJWT + JWKS token validation |
| MCP server | `mcp_server/` | FastMCP (streamable-HTTP), MSAL OBO |
| Agent | `agent/` | FastAPI + Google ADK, MSAL auth-code login |

## Prerequisites

1. **Three Entra ID app registrations**, wired with delegated permissions
   and admin consent. See **[`PREREQUISITES.md`](./PREREQUISITES.md)** for
   the exact portal click-path - do this first.
2. Python 3.11+.
3. A Gemini API key for the ADK agent's LLM calls (`GOOGLE_API_KEY`) -
   independent of the Entra setup. Get one at https://aistudio.google.com/apikey,
   or configure Vertex AI application-default credentials instead (see
   ADK docs).

## Environment variables

Each component has a `.env.example`. Copy to `.env` in the same directory
and fill in values from `PREREQUISITES.md`:

```
cp backend/.env.example backend/.env
cp mcp_server/.env.example mcp_server/.env
cp agent/.env.example agent/.env
```

Quick reference of which app registration each variable comes from:

| Variable | Used by | Source |
|---|---|---|
| `ENTRA_TENANT_ID` | all three | your tenant's Directory ID |
| `BACKEND_APP_CLIENT_ID` | backend, mcp_server | `poc-backend` app registration |
| `MCP_APP_CLIENT_ID` | mcp_server, agent | `poc-mcp` app registration |
| `MCP_APP_CLIENT_SECRET` | mcp_server | `poc-mcp` → Certificates & secrets |
| `AGENT_APP_CLIENT_ID` | agent | `poc-agent` app registration |
| `AGENT_APP_CLIENT_SECRET` | agent | `poc-agent` → Certificates & secrets |
| `AGENT_REDIRECT_URI` | agent | must match `poc-agent`'s registered redirect URI |
| `GOOGLE_API_KEY` | agent | Gemini API key (unrelated to Entra) |

## Run order

The three services must start backend-first (MCP depends on it being
reachable for its tools to do anything useful, and the agent depends on
MCP being reachable to register its toolset).

```
./run_local.sh
```

This creates a venv per component, installs pinned dependencies, and
starts all three in order (backend → mcp_server → agent), logging to
`/tmp/poc-backend.log`, `/tmp/poc-mcp_server.log`, `/tmp/poc-agent.log`.
Ctrl+C stops all three.

Or run each manually in separate terminals, in this order:

```
# 1. backend
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main          # http://127.0.0.1:8000

# 2. mcp_server
cd mcp_server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py            # http://127.0.0.1:8001/mcp

# 3. agent
cd agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py               # http://127.0.0.1:8501
```

Open **http://localhost:8501**.

## Testing each component independently

Use this section when you want to test bottom-up instead of through the UI:
backend alone → MCP server alone (via MCP Inspector) → agent alone → all
three together. It assumes the three app registrations, scopes, and admin
consent from `PREREQUISITES.md` already exist.

### 0. Get test tokens

You need real Entra access tokens to drive the backend or MCP server
directly (no browser UI involved). `scripts/get_token.py` mints one via
MSAL device-code flow - do the one-time setup in
[`PREREQUISITES.md` step 6](./PREREQUISITES.md#step-6---enable-manual-token-testing-optional-for-scriptsget_tokenpy)
first (enabling public client flows on `poc-agent`, or registering a
throwaway `poc-test-client`), then:

```
cd scripts && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# An MCP-audience token (for testing the MCP server / agent's MCP call)
.venv/bin/python get_token.py --client-id $AGENT_APP_CLIENT_ID \
    --scope api://$MCP_APP_CLIENT_ID/access_as_user
```

This prints a device-code URL/code to stderr - open it, sign in as your
test user, approve. Decoded claims (`aud`, `oid`, `scp`, ...) print to
stderr for sanity-checking; the raw token prints alone to stdout, so you
can capture it directly:

```
MCP_TOKEN=$(.venv/bin/python get_token.py --client-id $AGENT_APP_CLIENT_ID \
    --scope api://$MCP_APP_CLIENT_ID/access_as_user)
```

A backend-audience token needs a client with permission to `poc-backend`
directly - `poc-agent`/`poc-mcp` deliberately don't have that (see
PREREQUISITES.md option B for a dedicated `poc-test-client`):

```
BACKEND_TOKEN=$(.venv/bin/python get_token.py --client-id $TEST_CLIENT_ID \
    --scope api://$BACKEND_APP_CLIENT_ID/access_as_user)
```

### 1. Test the backend independently

```
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main          # http://127.0.0.1:8000
```

In another terminal:

```
# Public path - no token needed
curl -i http://127.0.0.1:8000/public/health
# -> 200 {"status":"ok","service":"poc-backend"}

# Private path, no token
curl -i http://127.0.0.1:8000/private/whoami
# -> 401

# Private path, real backend-audience token
curl -i http://127.0.0.1:8000/private/whoami -H "Authorization: Bearer $BACKEND_TOKEN"
# -> 200, your oid/preferred_username/scopes

# Private path, wrong-audience token (audience-confusion rejection)
curl -i http://127.0.0.1:8000/private/whoami -H "Authorization: Bearer $MCP_TOKEN"
# -> 401, aud doesn't match poc-backend
```

Watch the backend's stdout/log: successful calls log `oid`/`aud`, never
the token itself.

### 2. Test the MCP server independently with MCP Inspector

Keep the backend running (the MCP server's tools call it). Start the MCP
server:

```
cd mcp_server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py            # http://127.0.0.1:8001/mcp
```

In another terminal, launch MCP Inspector (no install needed):

```
npx @modelcontextprotocol/inspector
```

This opens a local web UI (default `http://localhost:6274`). Configure:
- **Transport type**: `Streamable HTTP`
- **URL**: `http://127.0.0.1:8001/mcp`
- **Authentication** → add a custom header: `Authorization: Bearer <MCP_TOKEN>`
  (use the `$MCP_TOKEN` minted in step 0 - this is the audience the MCP
  server's `JWTVerifier` actually accepts)

Click **Connect**. You should see the connection succeed and authenticate
(no auth header, or a backend-audience token, should fail to connect -
worth trying once to confirm the rejection). Then:
- Go to the **Tools** tab → **List Tools**. You should see `check_backend_health`
  and `get_my_identity`.
- Run `check_backend_health` with no arguments → expect the public health
  payload back.
- Run `get_my_identity` with no arguments → the MCP server performs the OBO
  exchange server-side and returns your identity as resolved by the
  *backend*. Watch `mcp_server`'s stdout: it logs the inbound call
  (`tool=get_my_identity oid=...`) and the OBO target audience.
- Try connecting with a `poc-backend`-audience token instead - Inspector
  should fail to connect (401, audience mismatch), demonstrating rejection
  in the other direction.

### 3. Test the agent independently

Keep the backend and MCP server running. Start the agent:

```
cd agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py               # http://127.0.0.1:8501
```

Open `http://localhost:8501` in a browser:
- You should be redirected to `login.microsoftonline.com`. Sign in as your
  test user.
- You land back on the chat UI showing your `preferred_username`.
- Ask **"is the backend up?"** → expect a reply derived from `check_backend_health`.
- Ask **"who am I?"** → expect your `oid`/`preferred_username` back, proving
  the agent's MCP-audience token flowed through MCP's OBO exchange to the
  backend and back.
- Click logout (or `POST /auth/logout`), confirm you're bounced back to the
  login page and a fresh visit requires signing in again.
- Optional: open a second browser (or incognito window) as a different test
  user and confirm the two chat sessions don't see each other's identity -
  each holds its own server-side MSAL token cache keyed by an opaque
  session cookie.

### 4. Test all three together

This is just step 3 with attention paid to the other two services' logs,
to see the full chain end-to-end:

```
./run_local.sh
```

or the three manual commands above in three terminals. Then, while asking
"who am I?" in the agent UI, tail the other two logs:

```
tail -f /tmp/poc-mcp_server.log /tmp/poc-backend.log
```

You should see the same `oid` appear in both, with the audience changing
from poc-mcp to poc-backend between the two log lines - see
["Logs show one oid, two audiences"](#4-logs-show-one-oid-two-audiences)
below for exactly what to expect.

## Verify the chain (acceptance criteria)

Each item below maps to a build-spec acceptance criterion.

### 1. Private endpoint rejects unauthenticated calls

```
curl -i http://127.0.0.1:8000/private/whoami
```
Expect `401 Unauthorized`. (Public health check works without a token:
`curl http://127.0.0.1:8000/public/health`.)

### 2. UI auth redirects through Entra

Visit `http://localhost:8501`. You should be redirected to
`login.microsoftonline.com` to sign in, then bounced back to
`/auth/callback` and land on the chat UI showing your username.

### 3. "Who am I?" proves the full chain

In the chat box, type **"who am I?"**. The agent should call the
`get_my_identity` MCP tool and reply with your `oid` and
`preferred_username` - retrieved by: agent (your MCP-audience token) →
MCP server (validates it, OBO-exchanges it) → backend (validates the
*new* token, returns your identity from its claims).

### 4. Logs show one oid, two audiences

Tail the three log files while you ask "who am I?":

```
tail -f /tmp/poc-mcp_server.log /tmp/poc-backend.log
```

You should see, in order:
- `poc_mcp` logs the inbound call: `tool=get_my_identity oid=<X> obo_target_aud=<backend-client-id>`
- `poc_backend` logs the OBO'd call: `authenticated request: oid=<X> aud=<backend-client-id-or-uri>`

Same `oid` both places; the audience changes from poc-mcp (implicit, via
the JWTVerifier accepting the request) to poc-backend (explicit, in the
backend's log line) - that's the OBO hop made visible.

### 5. Audience confusion is rejected (confused-deputy defense)

Mint a token for `poc-mcp` (e.g. via the MSAL device-code or
client-credential-of-a-test-client flow, or by reusing the token your
browser session got) and try it directly against the backend:

```
curl -i http://127.0.0.1:8000/private/whoami -H "Authorization: Bearer <poc-mcp-token>"
```
Expect `401` - `aud` doesn't match `poc-backend`. Symmetrically, a
poc-backend-audience token presented to the MCP server's endpoint is
rejected the same way (`aud` doesn't match `poc-mcp`).

A quick way to get a one-off MCP-audience token to test with is the MSAL
device-code flow against `poc-agent`'s client id, requesting scope
`api://<mcp-client-id>/access_as_user` - see `PREREQUISITES.md` if you
want to script this.

### 6. Public-path tool works

Ask **"is the backend up?"**. The agent should call `check_backend_health`
and reply with `{"status":"ok","service":"poc-backend"}`-derived text.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401` from backend or MCP server with a token that "should" work | Wrong audience | Confirm the token's `aud` claim (decode at jwt.ms) matches `BACKEND_APP_ID_URI`/`MCP_APP_ID_URI` exactly, including the `api://` prefix if your app uses it. |
| Login redirects to Entra but comes back with `AADSTS65001` | Missing admin consent | Re-check `PREREQUISITES.md` step 2/3 - **Grant admin consent** must show a green check on each delegated permission. |
| `get_my_identity` returns `{"error": "OBO exchange failed (invalid_grant): ..."}` | `poc-mcp` is missing the delegated permission to `poc-backend`'s `access_as_user` scope, or it wasn't admin-consented | Re-check `poc-mcp`'s API permissions blade. |
| `invalid_grant` mentioning "AADSTS70011" or scope mismatch | The OBO scope string doesn't match what's exposed | Confirm `BACKEND_APP_ID_URI` in `mcp_server/.env` matches the *exact* Application ID URI on `poc-backend`. |
| `401` only intermittently, near token expiry | Clock skew between machines | The backend's `CLOCK_SKEW_LEEWAY_SECONDS` (default 60s) absorbs small skew; if it's worse than that, fix system clocks (`timedatectl`/NTP). |
| Agent chat returns `{"error": "login_required", ...}` mid-conversation | Silent refresh failed (refresh token expired/revoked) | Expected behavior - the UI should redirect you to `/auth/login` again; sign in once more. |
| `ValueError: Unable to get authority configuration` at MSAL startup | `ENTRA_TENANT_ID` is wrong, or no network access to `login.microsoftonline.com` | Confirm the tenant GUID, check connectivity. |

## Design notes / deviations from a literal reading of the spec

- **Agent UI**: the spec suggests ADK's built-in web UI is fine, but that
  UI has no hook for per-user Entra login or per-request MCP
  `Authorization` headers. `agent/app.py` instead drives the ADK
  `Agent`/`McpToolset`/`Runner` programmatically from a small FastAPI app,
  documented inline in `agent/app.py`.
- **MCP inbound validation**: rather than hand-rolling PyJWT validation a
  second time, `mcp_server/server.py` uses FastMCP's built-in
  `JWTVerifier` resource-server auth provider, which performs the same
  JWKS/issuer/audience/expiry/scope checks declaratively. The raw JWT and
  decoded claims are read back via `fastmcp.server.dependencies.get_access_token()`
  - documented inline in `mcp_server/server.py`.
- **Per-request MCP connections**: `agent/mcp_agent.py` builds a fresh
  `McpToolset` (and tears it down) on every chat turn, so one user's
  bearer token can never end up on another user's MCP connection.
