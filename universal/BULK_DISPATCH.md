# Nationwide bulk route planning (private MVP)

DCC Universal can preview 300 fictional jobs in all 50 states, using 100
fictional contractors (two per state) from its development-only roster.
The integration does not use Terraboost or another employer's actual data.

## What it does

1. Open **Routes → Plan in bulk** in the same private workspace.
2. Choose an authorized task CSV or **Try 300 fictional jobs + 100 demo contractors**.
3. Choose the maximum number of physical stops per route (default: 2 for
   the fictional nationwide stress test).
4. Click **Preview grouped routes**. The server groups task lines at the
   same physical address into one stop, flags task IDs already reserved,
   considers eligible contractors *in the current company only*, and
   assigns stops to available contractors within their aerial service radius.
5. Inspect the number of proposed routes, matched tasks and exceptions.
   This preview takes no external routing API calls, sends no emails, does
   not reserve tasks, and **does not calculate mileage or pricing**.
6. Under pricing rules, enter the company's real approved fee formula.
   The default $20/stop is only a demo placeholder.
7. **Connect personal HeiGIT privately** before quoting real road miles.
   For safety, the first batch is limited to **three routes**; increase
   the per-run cap after reviewing a few saved route itineraries and prices.
8. Click **Calculate & create routes**, confirm explicitly, and leave the
   browser tab open. Each route uses the existing authoritative
   `POST /api/dispatch/routes` endpoint. It queries real road driving
   distance and time; calculates the dollar quote; and atomically reserves
   its task IDs. No contractor offers or messages are sent automatically.
9. If the browser closes or an API failure occurs, re-preview. Already
   reserved tasks are excluded so successful routes do not duplicate.

## CSV format

Required fields:

- `task_id` (or `external_task_id`)
- `venue`
- `address`
- `latitude`
- `longitude`

Optional: `task_type`, default Service.

Headers are mapped from some common synonyms. Unrecognized schemas need a
mapping enhancement, not guessed columns. Missing coordinates fail clearly:
automated address geocoding is a separate planned feature. Limit 2,000 task
lines per batch and 500 proposed routes per preview.

An authorized contractor roster must already exist inside the current
company; importer / managed connectors can provide it.

## Safety and scale

- Source workers are scoped to authenticated tenant. No global contractor
  pool or cross-company sharing.
- Inactive contractors are not selected; in-training contractors are optional.
- Service radius and batch clustering use aerial distances for fast candidate
  selection **only**.
- Address labels in the demo are synthetic and NON-DELIVERABLE. Geographic
  coordinates approximate city areas, not verified street entrances.
- HeiGIT Directions is required for road totals if that is the configured
  provider; the current Basic account's VROOM optimization permission is
  unverified, so route order uses multi-start geographic 2-opt with road
  totals confirmed by Directions.
- The hosted standard plan documents ~2,000 Directions requests per rolling
  24h and 40 requests/min. The browser sequences route creation and waits at
  least ~1.85 seconds between route starts, which is suitable for a *single
  interactive operator* with typical <=30-stop routes. It does not constitute
  a global cross-tenant rate limiter, durable background job queue or
  guaranteed dispatch throughput.
- No auto emails, contractor offers, money transfers or live assignments
  are executed by bulk planning.
- The route-creation loop runs in the browser; closing its tab interrupts
  further work. Saved routes remain committed. A durable server-side job
  worker with progress/resume is the next production step.
- This is a **private development preview** and is not yet approved for
  customers' confidential work or commercial production.

## HeiGIT configuration

The `HEIGIT_API_KEY` is read only from the server's private environment
and never passed in browser requests. Owner's Windows upgrade kit can
privately prompt for the key, pass it over previously verified SSH via stdin,
save it in a root-readable .env (0600), restart only the app container,
and run one real Directions call for verification.

The current provider URL is `https://api.heigit.org`; older
`api.openrouteservice.org` is deprecated. Confirm licensing, attribution,
caching and commercial plan requirements before selling to customers.
For larger production throughput, plan a supported licensed provider or
your own OSRM deployment and a durable job queue.
