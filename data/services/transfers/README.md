# Transfers Service

Migration status: in_progress

## What it does
Orchestrates transfers between accounts, including cross-currency transfers.
Locks an fx rate when currencies differ, then posts a single balanced entry
to the ledger for the converted amount.

## Dependencies
- ledger: every completed transfer posts a balanced entry here.
- fx: cross-currency transfers lock a rate here before posting to ledger.

## Gotchas new developers hit
1. createTransferUsingPOST locks an fx rate for 60 seconds via the fx
   service. If the transfer is not confirmed and posted to the ledger
   within that window, the lock silently expires. The transfer does not
   fail and does not notify you, it just re-quotes at whatever the current
   rate is when it finally posts. If you cached the originally quoted rate
   and displayed it to a customer, that number can now be stale and wrong.
2. getUsingGET_2 (the rate-lock detail endpoint) only returns data while
   the lock is still active. Once it expires it returns the same shape but
   with an empty rate field, it does not 404. Check expiresAt, not just
   HTTP status.
3. cancelUsingPOST_1 only works while status is PENDING. Once a transfer is
   COMPLETED the ledger entry is already posted and must be reversed
   through the ledger service directly, cancel will not do it for you.

## Migration notes
Transfers is mid-migration. ledger, one of its two dependencies, has
already completed its migration, so the ledger contract you build against
here is final. fx has not started its migration yet.
