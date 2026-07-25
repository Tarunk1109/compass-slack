# Auth Service

Owner: Identity Platform Team
Slack: #identity-platform
Criticality: crown-jewel
Migration status: migrated

## What it does
Issues and validates JWTs for customers and internal services. Every other
service trusts the tokens minted here, so an auth outage is a platform-wide
outage even though auth has no downstream dependencies of its own.

## Tech stack
Go, Redis for refresh token storage, RSA-signed JWTs (rotated keys published
via JWKS endpoint).

## Dependencies
None. Auth is a foundational service with no calls out to other services.

## Gotchas new developers hit
1. Access tokens are short-lived (15 minutes) by design. Do not cache them
   longer than that client-side; build refresh into your client instead of
   re-prompting for login.
2. Refresh tokens rotate on every use. If you refresh twice with the same
   old token (e.g. a retry storm), the second call gets 401. Only ever use
   the newest refresh token you were issued.
3. introspectToken is meant to be cached by callers for ~30 seconds. Calling
   it on every single downstream request will get you rate limited.

## Migration notes
Auth completed its migration to the current JWT-based flow. No legacy
session-cookie endpoints remain.
