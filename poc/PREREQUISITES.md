# Prerequisites: Entra ID App Registrations

This POC needs **three app registrations in one Entra ID tenant**, wired
together with delegated permissions and admin consent, before any of the
code in this repo will work. This document is the exact portal click-path
for setting that up. Do this once, then fill in the three `.env` files
(`backend/.env`, `mcp_server/.env`, `agent/.env`) from the values you
collect along the way.

You need **Global Administrator** or **Application Administrator** +
**Privileged Role Administrator** (or equivalent) rights in the tenant to
grant admin consent in step 3 below.

All permissions in this POC are **delegated** (acting as the signed-in
user). Do not grant or consent to any **application** permission anywhere
in this setup - that would defeat the point of the exercise (proving
delegated, per-user identity propagation).

---

## Overview of what you're building

| App registration | Type | Exposes scope | Has delegated permission to call | Has client secret | Has redirect URI |
|---|---|---|---|---|---|
| `poc-backend` | API | `access_as_user` | - | no | no |
| `poc-mcp` | API + confidential client | `access_as_user` | `poc-backend` / `access_as_user` | **yes** | no |
| `poc-agent` | confidential client | - | `poc-mcp` / `access_as_user` | **yes** | **yes** (`http://localhost:8501/auth/callback`) |

---

## Step 1 - Register `poc-backend`

1. Azure Portal → **Microsoft Entra ID** → **App registrations** → **New registration**.
2. Name: `poc-backend`. Supported account types: **Accounts in this organizational directory only** (single tenant). Redirect URI: leave blank.
3. **Register**.
4. Note down (you'll need these in `backend/.env` and the other two apps' `.env` files):
   - **Application (client) ID** → `BACKEND_APP_CLIENT_ID`
5. **Expose an API** (left nav):
   - Click **Add** next to "Application ID URI". Accept the default `api://<backend-client-id>` (or set your own) → **Save**. This becomes `BACKEND_APP_ID_URI`.
   - Click **Add a scope**:
     - Scope name: `access_as_user`
     - Who can consent: **Admins and users**
     - Admin consent display name: `Access poc-backend as the user`
     - Admin consent description: `Allows the app to call poc-backend's API on behalf of the signed-in user.`
     - User consent display name/description: similar, user-facing wording.
     - State: **Enabled**
   - **Add scope**.

You now have `api://<backend-client-id>/access_as_user`. No client secret, no redirect URI, no signed-in users hit this app directly - it's API-only.

---

## Step 2 - Register `poc-mcp`

1. **App registrations** → **New registration**.
2. Name: `poc-mcp`. Single tenant. Redirect URI: leave blank (this app is a backend service + API, not an interactive client).
3. **Register**. Note:
   - **Application (client) ID** → `MCP_APP_CLIENT_ID`
4. **Certificates & secrets** → **New client secret** → any description/expiry → **Add**.
   - Copy the secret **Value** immediately (it's hidden after you leave the page) → `MCP_APP_CLIENT_SECRET`.
5. **Expose an API**:
   - **Add** Application ID URI → accept default `api://<mcp-client-id>` → **Save** → this is `MCP_APP_ID_URI`.
   - **Add a scope**: same pattern as step 1 -
     - Scope name: `access_as_user`
     - Admins and users can consent
     - Display name/description as above, referencing poc-mcp
     - State: **Enabled**
6. **API permissions** (this is the OBO link to poc-backend):
   - **Add a permission** → **My APIs** → select `poc-backend`.
   - Choose **Delegated permissions** → check `access_as_user` → **Add permissions**.
   - Back on the API permissions page, click **Grant admin consent for `<tenant>`** → **Yes**. (Status column should turn to a green check.)

`poc-mcp` can now: (a) accept tokens minted for itself, and (b) perform OBO to mint tokens for `poc-backend` on behalf of whoever called it.

---

## Step 3 - Register `poc-agent`

1. **App registrations** → **New registration**.
2. Name: `poc-agent`. Single tenant.
3. Platform: click **Add a platform** → **Web** → Redirect URI: `http://localhost:8501/auth/callback` (must match `AGENT_REDIRECT_URI` exactly, including port). Leave "Implicit grant" checkboxes **unchecked** (this POC uses auth code flow, not implicit).
4. **Register**. Note:
   - **Application (client) ID** → `AGENT_APP_CLIENT_ID`
5. **Certificates & secrets** → **New client secret** → copy the value → `AGENT_APP_CLIENT_SECRET`.
   (`poc-agent` is a confidential client even though a human logs in interactively, because the redirect handler exchanges the auth code server-side and needs a credential to do so, and to silently refresh tokens later.)
6. **API permissions** (the link to poc-mcp):
   - **Add a permission** → **My APIs** → select `poc-mcp`.
   - **Delegated permissions** → check `access_as_user` → **Add permissions**.
   - **Grant admin consent for `<tenant>`** → **Yes**.

`poc-agent` can now request tokens scoped to `api://<mcp-client-id>/access_as_user` on behalf of whoever logs into the chat UI.

---

## Step 4 - Sanity-check the whole graph

In **Entra ID → App registrations**, for each of `poc-mcp` and `poc-agent`,
open **API permissions** and confirm:

- Exactly one delegated permission pointing at the *next* app in the chain (`poc-mcp` → `poc-backend`, `poc-agent` → `poc-mcp`).
- **Status = Granted for `<tenant>`** (green check) on that permission - this is the admin consent from steps 2/3. Without this, OBO/login will fail with `AADSTS65001` ("the user or administrator has not consented").
- No **Application** permissions anywhere (column should say "Delegated" only).

You should now have, scattered across your three `.env` files:

```
ENTRA_TENANT_ID                  (same value in all three .env files)

BACKEND_APP_CLIENT_ID            (backend/.env, mcp_server/.env)

MCP_APP_CLIENT_ID                (mcp_server/.env, agent/.env)
MCP_APP_CLIENT_SECRET            (mcp_server/.env only)

AGENT_APP_CLIENT_ID              (agent/.env)
AGENT_APP_CLIENT_SECRET          (agent/.env only)
AGENT_REDIRECT_URI=http://localhost:8501/auth/callback
```

See each component's `.env.example` for the full variable list (some are
derived/optional with sensible defaults).

---

## Step 5 - A test user

Use any account in the tenant (your own admin account works, or create a
test user under **Entra ID → Users → New user**). The first time that user
logs into `poc-agent`, Entra will show a consent screen for the
`poc-mcp/access_as_user` scope unless you granted **admin** consent in step
3 (which removes the per-user prompt). Either is fine for this POC; admin
consent makes the demo flow uninterrupted.

---

## Troubleshooting setup itself

- **"Need admin approval" on login**: the delegated permission exists but
  wasn't admin-consented. Go back to the relevant app's **API permissions**
  and click **Grant admin consent**.
- **AADSTS500011 "resource principal not found"**: the Application ID URI
  on the target app (e.g. `api://<mcp-client-id>`) doesn't match what the
  calling app requested. Double check `MCP_APP_ID_URI` / `BACKEND_APP_ID_URI`
  in your `.env` files match exactly what's set under each app's **Expose an API** blade.
- **Redirect URI mismatch on login**: the URI Entra redirects to must be
  byte-for-byte identical (scheme, host, port, path, trailing slash) to
  what's registered under `poc-agent`'s **Authentication** blade.

See `poc/README.md` for runtime troubleshooting (OBO `invalid_grant`,
audience mismatches, clock skew) once the apps themselves are registered.
