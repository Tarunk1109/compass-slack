# FX Service

Migration status: not_started

## What it does
Provides currency pair exchange rates and short-lived rate locks. Used by
transfers to quote a firm rate to customers during cross-currency moves.

## Dependencies
None documented at the API level. Rates come from a third-party provider
integration not modeled here.

## Gotchas new developers hit
1. Rate locks expire after 60 seconds and are not renewable. There is no
   extend or refresh endpoint, callers must request a new lock and accept
   whatever the current rate is.
2. getUsingGET_1 on an expired lock still returns 200, not 404. Check the
   expiresAt field yourself to know if the lock is still usable.
3. getUsingGET (single pair) and getUsingGET_2 (all pairs) can disagree
   for a few seconds around the 10-second refresh boundary, since they hit
   independently cached values. Do not assume they are read-consistent
   with each other.
