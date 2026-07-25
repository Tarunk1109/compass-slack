# Disputes Service

Migration status: not_started

## What it does
Manages customer disputes and chargebacks raised against settled payments,
including evidence collection and resolution.

## Dependencies
- payments: disputes are always opened against an existing paymentId.
- ledger: opening a dispute posts a provisional reversing entry pending
  investigation.

## Gotchas new developers hit
1. createUsingPOST posts a provisional reversing ledger entry immediately
   on dispute creation, before any investigation happens. If the dispute is
   later resolved MERCHANT_FAVOR, a second entry re-reverses the hold; the
   original payment's ledger history ends up with three entries, not one.
2. addEvidenceUsingPOST_1 does not change status away from OPEN by itself.
   Status only becomes EVIDENCE_SUBMITTED after a separate internal review
   step runs, which can lag behind the API call by several minutes.
3. resolveUsingPOST_1 is one-way. There is no endpoint to reopen a resolved
   dispute; a new dispute must be created instead.
