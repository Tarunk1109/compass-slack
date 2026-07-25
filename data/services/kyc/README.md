# KYC Service

Migration status: not_started

## What it does
Runs identity verification against uploaded documents and biographic data,
and stores the resulting risk rating and pass/fail outcome. Compliance
analysts can manually override an automated outcome.

## Dependencies
None. KYC is a leaf service that calls out to a third-party identity
verification vendor internally, not modeled in this API.

## Gotchas new developers hit
1. createUsingPOST returns immediately with outcome PENDING. Verification
   runs asynchronously against the vendor; you have to poll
   getUsingGET on the verificationId for the real result, it is not
   returned synchronously.
2. riskRating and outcome are separate fields. A verification can come back
   outcome PASS with riskRating HIGH. accounts.openAccount only checks
   outcome, not riskRating, so a high-risk-but-passed customer can still
   open an account today.
3. decisionUsingPOST overrides the outcome field but never touches
   riskRating. An analyst override does not change the underlying risk
   score.
