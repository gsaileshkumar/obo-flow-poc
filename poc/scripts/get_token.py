"""Mint a real, user-delegated Entra access token via MSAL device-code flow,
for manually testing one component at a time (no browser/UI needed).

This is dev tooling only - not used by any of the three running services.

Why device-code flow: it's the simplest interactive flow that doesn't need
a redirect URI or a client secret, so it works against any of this POC's
app registrations as long as "Allow public client flows" is enabled on
that app registration (Entra ID -> App registrations -> <app> ->
Authentication -> Advanced settings -> Allow public client flows -> Yes).
That toggle does not remove the app's existing Web redirect URI or secret -
it just additionally permits public-client flows like this one.

Usage:
    python get_token.py --client-id <client-id> --scope <scope> [--tenant-id <tenant-id>]

Examples:
    # An MCP-audience token, using poc-agent's client id (it already has
    # delegated permission to poc-mcp's scope - same token shape the real
    # agent obtains on login). Requires public client flows enabled on
    # poc-agent, or use a dedicated test client instead - see
    # ../PREREQUISITES.md "Testing" section.
    python get_token.py --client-id $AGENT_APP_CLIENT_ID \
        --scope api://$MCP_APP_CLIENT_ID/access_as_user

    # A poc-backend-audience token, using a test client that has delegated
    # permission to poc-backend's scope (poc-agent and poc-mcp do NOT have
    # this permission in the production identity graph, by design).
    python get_token.py --client-id $TEST_CLIENT_ID \
        --scope api://$BACKEND_APP_CLIENT_ID/access_as_user

The token is printed to stdout (capture it with $(...) for use in
Authorization headers); decoded claims are printed to stderr so stdout
stays clean for piping/capturing.
"""
import argparse
import json
import os
import sys

import jwt
import msal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client-id", required=True, help="Public-client-enabled app registration to authenticate as")
    parser.add_argument("--scope", required=True, help="Full scope URI, e.g. api://<app-id>/access_as_user")
    parser.add_argument("--tenant-id", default=os.environ.get("ENTRA_TENANT_ID"), help="Defaults to $ENTRA_TENANT_ID")
    args = parser.parse_args()

    if not args.tenant_id:
        print("error: --tenant-id not given and ENTRA_TENANT_ID not set", file=sys.stderr)
        return 1

    authority = f"https://login.microsoftonline.com/{args.tenant_id}"
    app = msal.PublicClientApplication(client_id=args.client_id, authority=authority)

    flow = app.initiate_device_flow(scopes=[args.scope])
    if "user_code" not in flow:
        print(f"error: could not start device flow: {flow}", file=sys.stderr)
        return 1

    # MSAL prints/formats this message itself; surface it on stderr.
    print(flow["message"], file=sys.stderr)

    result = app.acquire_token_by_device_flow(flow)  # blocks until the user completes sign-in

    if "access_token" not in result:
        print(f"error: token acquisition failed: {result.get('error')}: {result.get('error_description')}", file=sys.stderr)
        return 1

    token = result["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    print(
        json.dumps(
            {
                "aud": claims.get("aud"),
                "oid": claims.get("oid"),
                "preferred_username": claims.get("preferred_username") or claims.get("upn"),
                "scp": claims.get("scp"),
                "exp": claims.get("exp"),
            },
            indent=2,
        ),
        file=sys.stderr,
    )

    print(token)  # stdout: just the token, for capturing
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
