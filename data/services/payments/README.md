# Payments Service

Owner: Payments Platform Team
Slack: #payments-platform
Criticality: crown-jewel
Migration status: in_progress

## What it does
Handles the full payment lifecycle: create, retrieve, cancel, refund. It does not
hold balances. Balances live in the ledger service. Payments only orchestrates.

## Tech stack
Java 17, Spring Boot 3.2, Postgres 15, Kafka for settlement events.

## Dependencies
- fraud: synchronous pre-authorization check on every createPayment call.
- ledger: double-entry posting on create, cancel and refund.
- notifications: fires a customer email on refund only.

## Gotchas new developers hit
1. Idempotency-Key is required and must be UNIQUE PER INTENT. Replaying the same
   key does NOT return the original payment, it returns 409. This surprises almost
   everyone. If you want the original payment, call getPayment.
2. If the fraud service is down, createPayment returns 503. It fails closed on
   purpose. We never auto-approve a payment we could not screen. Do not "fix" this.
3. A SETTLED payment cannot be cancelled. Use refund instead. Cancel only works
   while status is PENDING.

## Migration notes
We are moving from the v1 mainframe-backed flow to the v2 event-driven flow.
v1 endpoints are frozen. Build against /v2 only. The ledger service has already
completed its migration, so the ledger contract you see today is the final one.
