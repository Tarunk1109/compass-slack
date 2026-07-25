# Statements Service

Migration status: not_started

## What it does
Generates and retrieves account statements over a date range, sourced from
the ledger service's entry history.

## Dependencies
- ledger: statement generation reads entries for the account and date range
  from the ledger service.

## Gotchas new developers hit
1. generateUsingPOST returns immediately with status GENERATING. The PDF is
   built asynchronously; poll getUsingGET on the statementId until status
   is READY before calling downloadUsingGET_1.
2. downloadUsingGET_1 on a statement that is not yet READY returns an empty
   PDF with a 200, not an error. Always check status first.
3. fromDate and toDate are inclusive on both ends, which is easy to get
   off-by-one on if you are used to exclusive end dates elsewhere in the
   platform.
