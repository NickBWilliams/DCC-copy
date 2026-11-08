# DCC Universal — Independent commercial foundation

Personal GitHub development branch: feature/dcc-universal-foundation.
This folder is a standalone application: no Terraboost, Railway, Mapbox or
company-owned account is required for local development.

## Accounts / ownership

* Primary personal owner: nicholas.byron.williams@gmail.com (verified GitHub account)
* Secondary approved personal email: nicholas.b.williams@outlook.com
* GitHub: NickBWilliams/DCC-copy (already personal)
* Routing: existing personal HeiGIT account under Gmail, with a Basic API key and Directions V2 entitlement; no key has been connected to the backend.
* Hosting: DigitalOcean personal account created and verified under Gmail; billing setup is incomplete, no Droplet exists. Railway is not used.
* Email delivery: SMTP account not configured.
* See ACCOUNT_SETUP.md and DEPLOY_DIGITALOCEAN.md for the independent deployment plan and outstanding paid-account activation.

The app never includes a purchased service token in browser JavaScript.

## Run locally with PostgreSQL and Docker

1. Copy .env.example to .env within this folder.
2. Change POSTGRES_PASSWORD to a long, unique alphanumeric value. Keep .env private.
3. Set DCC_OWNER_EMAIL to your own address (the example is already configured).
4. Start:

    docker compose up --build -d

5. Visit http://localhost:8000.
6. Enter the owner email and request a sign-in link. In **local development only**,
   a one-time link is printed to application logs:
   docker compose logs app
7. Click the printed link. Create routes and generate expiring external offer links.

There is intentionally **no shared admin password**. Contractors do not log in:
they use a one-time offer URL to accept, counter, or decline.

Docker Compose binds port 8000 to 127.0.0.1 by default. It is not public hosting.
Set DCC_ENV=production and configure **HTTPS and SMTP** before any public launch.
Do not expose development-mode login-link logs to customers.

## Routing configuration (no Mapbox)

For hosted testing, set the **existing personal HeiGIT key** as
HEIGIT_API_KEY in your private deployment environment, not in Git.
The current endpoints are:

* VROOM: https://api.heigit.org/vroom/v0
* Directions: https://api.heigit.org/openrouteservice/v2/directions/driving-car

The existing Basic Key shows Directions V2 access, but VROOM Optimization
access is unconfirmed. VROOM is **disabled by default**
(DCC_HEIGIT_USE_VROOM=false): the backend orders stops with multi-start
geographic 2-opt, then measures the complete depot-to-depot trip over the
real road network with HeiGIT Directions. Geographic order is a heuristic,
not a globally optimal road route. With VROOM entitlement, set
DCC_HEIGIT_USE_VROOM=true; if optimization fails, it falls back to the
geographic order without inventing road miles or durations.

For higher scale and fixed infrastructure costs, configure an independent
OSRM service in DCC_OSRM_BASE_URL. It takes precedence over HeiGIT and uses
the full-round-trip Trip API. The public OSRM demo is disabled by default and
must **not** be used as commercial product infrastructure.

US addresses use the keyless US Census street-address geocoder. Results are
cached separately per company; unknown addresses fail rather than using
unreliable city-level coordinates. This MVP does not geocode outside the US.

GET /api/routing/status shows which provider is configured for the signed-in
company. POST /api/routing/roundtrip accepts a home point and 1–90 stops:

    {"home":{"lon":-87.6298,"lat":41.8781},
     "stops":[{"lon":-87.62,"lat":41.89},{"lon":-87.67,"lat":41.88}]}

Returns road_miles, driving_hours, stop_order, provider and cached.

## Independent contractor roster and nearby matching

The staff-only **Contractor roster** panel stores independent contractor IDs,
names, optional email addresses, home coordinates, availability status
(active / in training / inactive), and an individual service radius.

- POST /api/contractors: create a company-specific contractor record.
- PUT /api/contractors/{external_id}: version-checked edits/deactivation.
- GET /api/contractors: list only the signed-in user's company roster.
- GET /api/contractors/nearby?lat=41.8&lon=-87.7&include_training=true:
  return eligible nearby contractors, sorted by geographic proximity.
  Optionally include_outside_radius=true to see those farther out.

For speed and zero incremental mapping API cost, proximity estimates are
**straight-line aerial miles**, visibly labeled as such. They are only used
to shortlist candidates relative to the center of the physical stops.
They are NOT presented as driving miles and NOT used to calculate pay.
Once a candidate is selected, the actual road-network round trip starts and
ends at that contractor's home address, with mileage, driving time and price
measured by the routing provider.

The directory creates **no contractor logins, dashboards or portals**.
It does not copy any data from Terraboost or existing IC rosters.
Any import of contractor information must be user-supplied and authorized.

## Real dispatch planning (not count-only routes)

The dashboard's **New route** panel now collects a contractor home coordinate,
actual task references, physical addresses, latitude/longitude and a configurable
pay policy. Tasks at the same physical address count as one stop; they remain
separate task IDs. Pasted lines use the format:

    Task ID | Venue | Full address | Latitude | Longitude | Task type

The dispatch workflow has three authenticated API calls:

- POST /api/dispatch/quote: calculate an ordered full round trip, true road
  mileage, driving hours and proposed pay, without reserving work.
- POST /api/dispatch/routes: calculate and create a ready-to-offer route,
  persist the ordered stop details and reserve every external task ID.
- GET /api/dispatch/routes/{route_id}: retrieve the saved ordered itinerary.
- POST /api/dispatch/routes/{route_id}/cancel with expected_version:
  cancel an unassigned/ready/flagged/declined route, release reservations,
  preserve route history; an active sent offer must first be revoked.

Example request for quote and route creation:

```json
{
  "title": "Southwest loop",
  "contractor": "Sample independent contractor",
  "home": {"lat": 41.8, "lon": -87.7},
  "tasks": [
    {"external_task_id":"JOB-101","venue":"Example Market",
     "address":"100 Main St, Chicago, IL 60601","lat":41.878,"lon":-87.630,
     "task_type":"Install"}
  ],
  "pricing": {"base_fee":10,"per_stop":5,"per_mile":0.5,
              "per_hour":10,"minimum":20}
}
```

Proposed pay is max(minimum, base_fee + stop_count*per_stop +
road_miles*per_mile + driving_hours*per_hour), rounded half-up to cents.
Rates are **company configurable per quote**, never fixed to Terraboost rates.
This is a calculation, not an automatically authorized payment.

Task reservation is unique by normalized (company, external task ID) in the
database. A duplicate or racing request returns HTTP 409. Cancellation
releases reservations for reassignment but never deletes history. Routes
already accepted/finalized/completed cannot be cancelled by this operation.

The contractor offer page displays the actual ordered itinerary; contractors
still do not receive accounts or access the dashboard. The existing legacy
count-only route endpoint is retained for compatibility, but cannot provide a
physical itinerary.

**Limitations:** imports from external systems, address-to-coordinate batch
matching, delivery notifications, optimization
jobs, and task-level completion reconciliation are not yet implemented.

## Staff access and external offers

DCC_OWNER_EMAIL / DCC_ORGANIZATION_SLUG bootstrap the first owner and company.
The owner may add staff with POST /api/staff after signing in. Other companies
can use the membership model, but automated company self-signup and billing
have not been implemented. Auth sessions last seven days and are stored hashed.

Contractor offers are generated using POST /api/routes/{id}/offers. The link
is displayed and can be copied; **no email or SMS is sent automatically yet**.
Responses (Accept, Counteroffer, Decline) are recorded against a one-time,
revocable 48-hour offer token. Dispatchers cannot impersonate the contractor
through the route transition API.

## Tests

    python -m pip install -r requirements.txt
    python -m pytest -q

GitHub Actions CI in .github/workflows/dcc-universal.yml runs tests and builds
the container. Provider calls are mocked in tests (no API key needed).

## Before production launch

This is an **MVP**, not a customer-ready SaaS. Remaining: Alembic database
migrations instead of create_all, rate limits, cross-instance token redemption
and session concurrency hardening, account provisioning, organization membership
administration, optional email/SMS delivery with delivery acknowledgments, route
stop/task identity and reservations, jobs, backups, observability, and legal review
of external mapping/geocoding terms (including attribution, result licensing,
and caching permissions). Legacy DCC and DCC Revamp are untouched.
