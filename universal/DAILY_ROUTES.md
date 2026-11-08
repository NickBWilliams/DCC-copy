# Daily route planning and accepted records

New dispatch plans use directed road travel-time and distance matrices. They are
split into daily loops starting and ending at the contractor's home. All physical
stops stay intact; service time adds across task lines at an address. The default
onsite estimate is 30 minutes per task and must be reviewed for the actual work.

Defaults: target 8 hours, maximum 10 hours, 30-minute break, 08:00 local start.
The total includes driving, onsite work, breaks and waiting for venue access.
A lone stop may exceed the target but never the maximum; this is flagged. An
infeasible stop blocks the entire quote before task claims are created. Break
placement is conservatively reserved before service at the first venue.

This is a deterministic road-cost insertion/2-opt heuristic, not a guarantee of
the global optimum. Venue windows repeat daily and use local minutes after
midnight. Cross-timezone, overnight lodging, dated appointments, live traffic,
and calendar availability are not automatically planned in this version.

## Company pricing

`GET/PUT /api/planning-settings` stores defaults per company; writes require
owner/admin and matching version. Omitted quote policies use the company's
saved settings. Supplied policies are explicit full overrides for that quote.
The route builder can load/save defaults, adjust onsite time and daily limits,
and override the total offer. Existing saved/accepted routes are not repriced.

Base fee and minimum apply per day. Per-mile pay includes every daily outbound
and return leg. The hourly rate can apply to driving only or total daily time (including onsite,
breaks and waiting). An additional onsite-hour rate is optional;
avoid double-paying work already included in a stop rate. Total-offer overrides
are allocated proportionately across days with a final rounding adjustment.

## Road provider

Daily planning needs either OSRM Table (durations AND distances) or HeiGIT/ORS
Matrix entitlement. A Directions-only key may not support Matrix; the request
fails explicitly with no quote or reservation. Matrix calls are batched at up to
25 origins by 25 destinations; no aerial-cost fallback is allowed. Provider
quota/rate-limit responses stop the bulk workflow. Road measurements and map
tiles use the existing non-Google providers. Navigation can open Apple Maps,
Google Maps, or Waze for a selected destination; the complete itinerary stays in
one private application URL, with daily loops shown separately.

## Acceptance

Acceptance creates an immutable snapshot and PDF in a new additive table,
`dcc_route_receipts`, in the same transaction as the decision. The accepted
view is `/accepted/{token}`, separate from the spent offer. The original response
endpoint still rejects all further decisions. The accepted view stays available
after offer expiry while the route is accepted, finalized or completed, unless
staff revoke access. It includes a map, days, details and the PDF; no response
controls or mutating contractor endpoints are provided.

The PDF and confirmation-email status are available on the dispatch route.
Snapshots preserve agreed pay, typed acknowledgment, contractor notes, itinerary
and daily totals. Company changes do not rewrite these records. Legacy routes
without recorded itineraries cannot acquire missing stop details retrospectively.

Confirmation email is attempted after the acceptance commits, to one uniquely
matched eligible contractor email in that company. The PDF is attached and the
HTML contains a clickable read-only route link. Public HTTPS and SMTP must be
configured; localhost remains blocked. Database receipt status is authoritative:
`pending`, `sending`, `sent`, `blocked`, or `unknown`. SMTP acceptance is not proof
of inbox delivery. Blocked/pending deliveries can be retried from dispatch. A
crash during sending or ambiguous SMTP exception needs operator investigation;
it is not automatically retried and cannot cause another acceptance. Pending
records after a restart are retained for manual retry, not a guaranteed outbox
worker. No real emails were sent during development tests.

Private URLs contain bearer credentials. Do not forward documents or URLs; staff
can revoke accepted access with the company-scoped API. Database snapshots and
PDFs contain those URLs and must be protected with the rest of company data.
