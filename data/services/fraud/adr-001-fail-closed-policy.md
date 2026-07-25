# ADR-001: Fail-closed on fraud check failure

## Status
Accepted

## Context
Fraud checks sit synchronously in front of high-value actions like payment
creation and card issuance. Early on, callers asked what should happen if
the fraud service is unreachable, times out, or a rule throws an error
during evaluation. The tempting answer is to approve and let a downstream
process catch anything bad later.

## Decision
Any failure to reach a clean APPROVE or REVIEW outcome results in DECLINE.
This applies to network failures, timeouts, and rule evaluation errors
alike. There is no code path that auto-approves when fraud cannot complete
its evaluation.

## Consequences
- Outages or bad rule deploys in fraud cause real, visible payment failures
  instead of silent risk exposure. This is intentional; it makes failures
  loud instead of invisible.
- Callers (payments, cards) must design their own UX and retry behavior
  around getting a DECLINE that was actually caused by an infrastructure
  problem rather than a real risk signal. Today this is not distinguishable
  from the response alone.
- Anyone tempted to "fix" a fraud outage by defaulting to APPROVE is
  reversing a deliberate risk decision, not fixing a bug.
