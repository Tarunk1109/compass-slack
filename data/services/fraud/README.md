# Fraud Service

Migration status: not_started

## What it does
Runs fraud checks on payments, card transactions, and transfers, and tracks
manual review cases. Auto-generated API docs, ask in the team channel if
something here does not match what you see in code.

## Dependencies
None documented at the API level. In practice the check endpoint is called
synchronously by payments and cards before those services commit anything.

## Gotchas new developers hit
1. If a rule fails to evaluate (bad expression, missing field on the
   entity, whatever), the whole check fails closed and returns DECLINE, not
   REVIEW. People expect a broken rule to degrade to "send it to a human,"
   it does not. A malformed rule you just deployed can silently start
   declining real payments.
2. checkUsingPOST does not enforce any timeout on its own side. The 800ms
   budget you see mentioned in the payments service docs is enforced by the
   caller, not by fraud. If fraud is slow, it will just keep processing
   after the caller has already given up and failed the payment closed.
3. Cases created via /api/fraud/cases are not linked back into the decision
   for the original check. Opening a case does not retroactively change a
   DECLINE into anything else; that has to happen out of band.
