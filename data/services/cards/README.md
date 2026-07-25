# Cards Service

Owner: Cards Team
Slack: #cards-team
Criticality: high
Migration status: not_started

## What it does
Issues and manages debit and credit cards linked to customer accounts, and
exposes the authorization/settlement history for each card.

## Tech stack
Java 17, Spring Boot 3.2, integrates with a third-party card network
processor for authorization routing.

## Dependencies
- ledger: issueCard posts a holding entry for the card program float.
- fraud: issueCard runs a fraud pre-check on the cardholder before issuing.

## Gotchas new developers hit
1. maskedPan is always masked in API responses, there is no endpoint that
   returns the full PAN. Full PAN only ever exists inside the card network
   processor integration, never in our own database.
2. A card must be activated with activateCard before it can authorize
   transactions, even if issueCard returned 201. Issued and active are
   different states.
3. freezeCard blocks new authorizations immediately but does not reverse
   authorizations already in flight. Those settle normally.

## Migration notes
Cards has not yet started its migration. This is the legacy v1 API; expect
it to change once the migration work is scheduled.
