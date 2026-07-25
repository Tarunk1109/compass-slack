# ADR-001: Double-entry accounting model

## Status
Accepted

## Context
Early versions of the ledger service stored a single running balance per
account and applied deltas directly. This made it impossible to reconstruct
how a balance was reached, made concurrent updates race-prone, and gave
auditors no trail to follow during regulatory review.

## Decision
Every change to a balance is recorded as a double-entry record: one debit
leg and one credit leg that must sum to zero in the same currency. Entries
are immutable and append-only. Balances are always derived by summing
entries, never stored and mutated directly. To undo an entry, a reversing
entry is posted; nothing is ever deleted or updated in place.

## Consequences
- Balances are always reconstructable and auditable from the entry log.
- Every write is one extra call (reverse instead of update), which is a
  small ergonomic cost callers have to design around.
- Cross-currency movements must be converted to a single currency before
  posting, since a single entry cannot balance across two currencies.
- This is the reason ledger's API has stayed stable through its migration:
  the model does not need to change as new services are added, only new
  callers of postEntry.
