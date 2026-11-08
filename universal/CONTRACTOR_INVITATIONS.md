# Contractor route invitations — map-first experience

## Verify the contractor page without a staff account

In the existing personal DigitalOcean preview, keep the SSH tunnel open and
visit **http://localhost:8000/demo/route-invitation**. A shortcut also appears
on the staff sign-in screen when the app is in localhost development mode.

This is a full responsive contractor page, matching the new SaaS design:
a branded route hero, payout / stops / due / driving-time metrics,
a coordinate-based schematic map visible immediately, an optional interactive
tile map, task breakdown, ordered stop cards and Accept / Counter / Decline
forms.

- The demo uses **fictional** contractor, route and work details.
- **1 stop** and **6 stops** examples are available at the top.
- Demo responses are simulated inside the browser and do not call the
  contractor response API, reserve tasks, send mail, create offers or
  modify any database record.
- Real staff APIs still require authorized login; real contractor offers
  still require a unique, revocable, expiring invitation token.
- Demo endpoints return **404 outside development + localhost**. They do
  not create a general access bypass on a commercial deployment.
- To evaluate the mobile layout on Windows Chrome press **F12**, then
  **Ctrl+Shift+M**, and select a phone viewport. A physical phone
  outside the PC cannot access a localhost-only SSH tunnel without
  separately configured secure hosting.

This route-page preview is not the same as signing into the staff dashboard.
It is specifically designed to let the product owner approve contractor UX
without granting anyone access to the company's internal workspace.

## Contractor flow (existing company-scoped route workflow)

The route invitation can be shared using a revocable one-time link. The
contractor does **not** need an account or access to the staff workspace.

1. A dispatcher reviews a **Ready** route and its verified payout.
2. In the route's **More actions** menu they can **Preview invitation email**
   without making an offer or sending anything.
3. **Create shareable offer link** issues a 48-hour private link. This remains
   a manual-copy workflow; do not assume that clicking it sends an email.
4. When a public HTTPS domain, SMTP and a uniquely matched non-demo roster
   email are configured, a dispatcher can choose **Email contractor**. This
   is a separate explicit action that sends an HTML invitation plus a plain
   text fallback with a link to the private offer page.
5. Contractor opens their invitation and sees the company display name,
   route title/work order, proposed pay, due date if supplied, stop/task
   counts, verified road-mile and driving-hour estimates when available.
6. Contractor can open the **interactive numbered stop map**, review the
   full ordered stop list and task types, and open Google Maps directions in
   chunks with no API key required.
7. Contractor chooses **Accept**, **Counter**, or **Decline**. Accept requires
   full legal name, confirmation checkbox and versioned acknowledgment text;
   the response is audited. Once used, revoked or expired, the private offer
   no longer exposes the route details or allows further responses.

**Critical map distinction:** The embedded map shows coordinates and stop
order with straight/dashed schematic connectors, NOT a turn-by-turn route
geometry. The authoritative driving miles/hours/pay were calculated using
the server's configured route provider before the offer was issued. The
Google Maps navigation link may recalculate directions or segment large
itineraries; contractors should follow the ordered stops and verify the
route before travel.

## Privacy and mapping infrastructure

Offer links are bearer credentials, scoped to one route, and expire after
48 hours. Do not put these links in public chats, screen captures, analytics
or any shared knowledge base.

- **Development:** in-page map uses the public OpenStreetMap tile service
  only when the contractor intentionally presses *View route map*. Map
  tiles are not prefetched. The tile service receives the site origin, not
  the path containing the private offer token. Attribution is visible.
- **Commercial production:** set a licensed/self-hosted tile URL and
  attribution through **DCC_MAP_TILES_URL** and
  **DCC_MAP_TILES_ATTRIBUTION** in the server's untracked .env. Without an
  approved provider the in-page map is disabled, but the Google Maps
  directions links continue to work. Do not rely on donated OSMF tiles for
  paid usage.
- The tile URL is public browser configuration, so do NOT put a secret
  server-only HeiGIT API key in it. A public browser-restricted map token
  may be appropriate with a licensed tile provider.
- The offer page's Referrer-Policy is **origin**. It sends only the host to
  map tile services and never the bearer token URL. Other API pages retain
  no-referrer policy.
- Google Maps links contain only stop/depot coordinates, never the private
  invitation token. A long route is split into at most three intermediate
  waypoints per navigation link for compatibility with mobile browsers.
- Customer company branding and acknowledgment terms are maintained
  separately in the existing offer settings API.
- No corporate data or Terraboost-owned contact list is used for demos.

## Email delivery prerequisites

The personal DigitalOcean preview is accessible through an SSH tunnel at
http://localhost:8000. A contractor outside that Windows PC cannot use
that URL. Outbound contractor email remains **disabled until all of the
following are configured**:

1. Public DNS name with working HTTPS.
2. DCC_PUBLIC_URL set to that exact public HTTPS base.
3. DCC_SMTP_HOST, DCC_SMTP_PORT, DCC_SMTP_FROM, optionally
   DCC_SMTP_USER and DCC_SMTP_PASSWORD, stored privately on server.
4. The current customer has one uniquely matching, eligible, non-demo
   contractor in its own roster, with a valid email address. Ambiguous
   names do not cause automated delivery.

No outbound email is generated by creating a route, previewing an email,
batch planning, or viewing the map. The explicit *Email contractor* button
is required.

SMTP delivery and database commit are currently an MVP synchronous
operation; a future durable outbox queue should guarantee recovery if a
message is delivered but the database commit fails. Vendor-specific
sending rules and customer-by-customer sender identities should also be
implemented before a multi-tenant commercial launch.

## References

- Google Maps URLs API:
  https://developers.google.com/maps/documentation/urls/get-started
- OpenStreetMap Foundation tile usage policy:
  https://operations.osmfoundation.org/policies/tiles/
