# Ledger Service

Owner: Core Banking Team
Slack: #core-banking
Criticality: crown-jewel
Migration status: migrated

## What it does
System of record for account balances. Holds every debit and credit as an
append-only double-entry record. It does not know about payments, transfers,
or cards, it only knows about legs and balances. Every other financial
service calls in here to actually move money.

## Tech stack
Kotlin, Spring Boot, Postgres 15 with an append-only entries table (no
UPDATE or DELETE grants on the table, enforced at the DB role level).

## Dependencies
None. Ledger is foundational; nothing it does requires calling out to
another service.

## Gotchas new developers hit
1. Entries are append-only. There is no update or delete endpoint, and there
   never will be. To undo an entry, post a reversing entry with reverseEntry.
2. postEntry requires debit and credit legs to balance exactly in the same
   currency. Cross-currency movements must be converted to a single currency
   by the caller (see the fx service) before posting.
3. getBalance only reflects settled entries. If you just posted an entry and
   immediately call getBalance, allow for a brief propagation delay in
   non-prod environments.

## Migration notes
Ledger has completed its migration to the current schema and API. This is
the final contract; nothing about postEntry, reverseEntry, or the balance
endpoints is expected to change. Every service that depends on ledger can
build against this with confidence.
