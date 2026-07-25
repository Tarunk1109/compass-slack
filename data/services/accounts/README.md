# Accounts Service

Owner: Accounts Team
Slack: #accounts-team
Criticality: high
Migration status: in_progress

## What it does
Manages customer account records: opening, closing, updates, and profile
lookups. It does not hold balances, that is the ledger service's job.

## Tech stack
Java 17, Spring Boot 3.2, Postgres 15.

## Dependencies
- kyc: openAccount requires a passed KYC verification id. Accounts will not
  open an account for a customer who has not cleared identity verification.

## Gotchas new developers hit
1. openAccount takes a kycVerificationId, not a customerId lookup. You must
   call kyc yourself first and pass the resulting verification id; accounts
   does not call kyc for you.
2. closeAccount checks the ledger balance before closing and returns 409 if
   it is nonzero. Zero out the balance via a ledger transfer first.
3. currency is set at account open time and is immutable. There is no
   endpoint to change an account's currency after opening.

## Migration notes
Accounts is mid-migration to the v1 API shown here. The old v0 endpoints
still exist internally but are not documented; do not build against
anything outside /v1.
