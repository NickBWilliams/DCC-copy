# DigitalOcean deployment plan (no Railway, no Mapbox)

This is a deployment **preparation guide**, not evidence that a server is live.
Only personal accounts and credentials should be used. The existing DCC Revamp
service and Terraboost resources must not be migrated or modified.

## Current personal DigitalOcean status (verified 2026-11-07)

- Signed in at cloud.digitalocean.com as nicholas.byron.williams@gmail.com.
- Account still shows **Incomplete / Finish Account Set Up**.
- Payment method is missing; DigitalOcean says billing ACTION NEEDED.
- Signup credit $5 remaining; no Droplets running.
- A default project "first-project" exists but is not used by this app.
- No VPS, database, domain, SSL certificate or SMTP service is provisioned.

Do not assume the signup credit covers a full month of hosting.
No paid resources were created during account verification.

## Prerequisites before provisioning

1. The account owner completes DigitalOcean's payment-method verification
   directly in DigitalOcean. Do not share card data or passwords in chat.
2. Confirm whether the independent source repository is legally usable and
   review/public visibility; keep company-owned code and data out of this app.
3. Choose a domain under personal ownership. Set DNS A record to the
   future Droplet public IPv4. HTTPS via Caddy requires reachable port 80/443.
4. Create a personal SSH key and register the PUBLIC key in DigitalOcean.
   Never send private SSH keys through chat or check them into Git.
5. Create/activate a separate personal SMTP provider to deliver staff
   sign-in links. The app intentionally does not expose dev-only magic links
   on a production server.
6. Prepare the existing personal HeiGIT Directions key for secret-only runtime
   configuration. Never paste the key in GitHub code or into a public URL.
7. Obtain explicit budget/size approval **before** creating a billable Droplet.

## Proposed topology

- One DigitalOcean Ubuntu LTS Droplet dedicated to DCC Universal.
- Docker Engine + Docker Compose plugin.
- Caddy receives ports 80 and 443, obtains TLS certificates, and proxies
  internally to a FastAPI/Uvicorn app container.
- PostgreSQL is on a **private Docker network** and has no public host port.
- Three named Docker volumes retain database and TLS certificate data.
- HeiGIT is accessed by the app container using the key stored in a local,
  untracked file. No Mapbox or Railway.
- No external contractor or company integrations are enabled by default.

Files:
- compose.production.yaml: containers, internal network and secure defaults.
- Caddyfile: TLS ingress and security headers.
- .env.production.example: names of required secret/runtime variables.
- .dockerignore: protects local .env secrets from the built image.

## Deployment checklist (after account and billing activation)

On the approved personal server, from the independent repository's
"universal" directory:

1. Copy .env.production.example to .env.production, then set permissions to
   owner read/write only (chmod 600). Set a long **alphanumeric** database
   password; passwords containing URL reserved characters need DSN encoding.
2. Set DCC_DOMAIN to an actually owned DNS name pointing at the Droplet.
3. Set DCC_OWNER_EMAIL to the approved Gmail or Outlook personal address.
4. Enter HEIGIT_API_KEY and SMTP settings **locally**. Never commit secrets.
5. Run:

       docker compose -f compose.production.yaml --env-file .env.production config --quiet
       docker compose -f compose.production.yaml --env-file .env.production up -d --build

6. After DNS and TLS are active, test https://YOUR_DOMAIN/health .
   Expect {"status":"ok"}. Request a staff login link at the public
   HTTPS app, then verify the email arrives and sign-in works.
7. Check that the DigitalOcean firewall allows only SSH from trusted IPs,
   HTTP 80 and HTTPS 443 publicly. Do not open the Postgres port 5432.
8. Validate a real HeiGIT Directions call with a small **independent test**
   route, checking returned road miles and full stop order, plus quota.
9. Back up PostgreSQL (encrypted off-server), configure restore testing,
   updates, uptime monitoring and bounded retention before customer data.

## Important limitations

- This is a **pre-production** stack. Real SaaS still needs migration tooling,
  backups/restore automation, rate limiting, secret rotation, CSRF controls,
  account management, billing and external integration hardening.
- A single Droplet with database in Docker is inexpensive but not high
  availability. Disk loss without external backups would lose state.
- DNS/SMTP/HeiGIT keys are not installed yet; a usable public app URL does
  not exist.
- Creating a Droplet and enabling running resources incurs ongoing charges
  independently of whether the product receives traffic.
