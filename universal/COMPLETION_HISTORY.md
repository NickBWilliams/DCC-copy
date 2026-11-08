# Completion and history

Open **Completion & history** in the dispatch sidebar, or `/history`.

The company-scoped screen searches route name/ID, contractor, venue/address, and external task ID. Filter by route stage, payment stage, or route completion date (UTC). Completion date is the latest completion timestamp among a route's task records. Legacy routes with no dated task records cannot match completion-date filters.

Each route shows its immutable accepted PDF, staff-only full map, confirmation email result, current task states, notes, HTTPS photo links, and append-only activity. Historic completion updates retain their original notes/photos when later updates supersede them. Photos remain links to the company's source system; this release does not copy or permanently host image bytes. Source permissions and URL expiration still apply.

Staff can update individual tasks. Owner/admin users can import updates, configure a completion feed, and track payments. All tasks must be completed before a planned route is marked Completed. Failed/removed work stays visible and does not silently count as successful completion. A correction can reopen a completed route. Payment events are retained; work changed after submission/payment creates a payment-review event. No payment is sent by this application.

## Acceptance verification

`GET /api/acceptance-readiness` reports the deployment's public-HTTPS and SMTP readiness. Each accepted route reports sent/blocked/unknown separately. The confirmation includes agreed pay, the private read-only map URL, daily hours/miles, and the stored PDF attachment. Acceptance/counter/decline remain closed after acceptance. A blocked message may be retried after configuration is corrected. An uncertain send is not automatically retried, avoiding duplicate delivery.

Localhost preview cannot be emailed to contractors: configure the production HTTPS domain and SMTP settings first. Tests use a fake SMTP transport while exercising real MIME assembly, PDF bytes, and acceptance processing. They do not establish delivery to a real external mailbox.

## Company completion feed

This release includes a generic normalized HTTPS JSON feed and JSON-file import. Native Onfleet/Monday/other vendor payloads must be translated to the schema below by a company adapter or integration workflow. No native vendor connection is implied. Existing contractor-roster integrations remain separate.

Owner/admin: expand **Completion feed & imports**, save the URL and Bearer token, then select **Sync now**. Credentials use the existing `DCC_CONNECTOR_ENCRYPTION_KEY`. Production requires the feed hostname in `DCC_CONNECTOR_ALLOWED_HOSTS`. Only HTTPS port 443 and public DNS are supported; redirects are refused; response limit is 4 MB. No background polling is enabled. Empty feeds are valid and change nothing.

```json
{"updates":[{
  "route_id":12,
  "external_task_id":"job-123",
  "event_id":"job-123-update-2",
  "status":"completed",
  "occurred_at":"2026-11-01T18:00:00Z",
  "completed_at":"2026-11-01T17:45:00Z",
  "notes":"Artwork installed",
  "photo_urls":["https://company.example/photos/123.jpg"]
}]}
```

Supported states: `pending`, `in_progress`, `completed`, `failed`, `removed`. Include timezone offsets in timestamps. If completed_at is omitted for completed work, occurred_at is used. A task must already belong to the specified route in the current company. A missing task in a feed never implies removal.

One batch is atomic (maximum 1,000 updates). Repeat event IDs with identical content are deduplicated; reuse with different content is rejected. Older/equal-timestamp events are retained as history without replacing newer state. Explicit newer corrections may reopen work. Route version checks prevent stale manual updates. IDs are scoped by company and source (`manual`, `import`, `feed`).

Endpoints:

- GET `/api/history`, GET `/api/history/{route_id}`
- POST `/api/completions/task` (adds expected_version to one update)
- POST `/api/completions/import` (normalized batch, owner/admin)
- GET/PUT `/api/completions/feed`, POST `/api/completions/feed/sync` (owner/admin)
- PUT `/api/routes/{route_id}/payment` (status, expected_version, reference; owner/admin)
- GET `/routes/{route_id}/map` and `/api/routes/{route_id}/acceptance/view` (staff scoped)

New tables are additive and created at application startup. Existing routes, contractor records, accepted documents, and pricing are preserved. No credentials or live company feeds are bundled.
