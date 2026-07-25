# Notifications Service

Owner: Comms Platform Team
Slack: #comms-platform
Criticality: medium
Migration status: migrated

## What it does
Sends transactional email, SMS, and push notifications, and stores
per-customer notification preferences. Used by payments (refund receipts)
and other services that need to reach a customer.

## Tech stack
Node.js, SQS for the delivery queue, SendGrid and Twilio as delivery
providers.

## Dependencies
None. Notifications is a leaf service; nothing calls out from here except
to third-party delivery providers.

## Gotchas new developers hit
1. sendEmail and sendSms return 202 as soon as the message is queued, not
   once it is delivered. Poll getNotification if you need to confirm actual
   delivery, do not assume 202 means the customer received it.
2. If a customer has opted out of a category, the send is silently skipped,
   not rejected. You get a 202 back with status SKIPPED_PREFERENCE, not an
   error. Check the response status, not just the HTTP code.
3. SECURITY_ALERT category ignores preferences entirely and always sends.
   This is intentional and cannot be overridden by updatePreferences.

## Migration notes
Notifications has completed its migration. The old email-only v0 API has
been fully decommissioned.
