# DigitalOcean deployment plan (no Railway, no Mapbox)

This is a deployment **preparation guide**, not evidence that a server is live.
Only personal accounts and credentials should be used. The existing DCC Revamp
service and Terraboost resources must not be migrated or modified.

## Current personal DigitalOcean status (verified 2026-11-07)

- Personal DigitalOcean Droplet `dcc-universal-dev` is active at
  `104.236.113.110`, Ubuntu 24.04.5 LTS, NYC3, $12/month.
- DigitalOcean Cloud Firewall inbound allows SSH TCP/22; app/database ports
  have not been opened publicly.
- The owner validated the exact Droplet ED25519 host-key fingerprint using the
  DigitalOcean console, then successfully connected via the Windows SSH kit.
- **Docker Engine 29.9.3 and Docker Compose 5.7.0 were installed successfully**
  on the Droplet using `scripts/bootstrap_ubuntu_digitalocean.sh`.
  The user's install screen confirmed both versions and displayed the successful
  completion banner.
- SSH key installation and authentication from the owner's Windows PC work.
  The bootstrap key is unencrypted: replace it with an owner-generated,
  passphrase-protected key when practical.
- **The DCC Universal app itself is not yet deployed.** A private development
  preview through an SSH tunnel is the next staging step.
- No domain, HTTPS, SMTP or HeiGIT secret is configured yet. Do not expose the
  development-mode email-link login to the public internet.
- The personal GitHub repository is presently PUBLIC; review legal ownership
  and source provenance before any commercial release or customer data use.

## Prerequisites before provisioning

1. **Completed:** the account owner activated DigitalOcean billing. Do not share card data or passwords in chat.
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
   Proposed initial development size: DigitalOcean Basic Regular SSD, 2 GiB,
   1 vCPU, 50 GiB disk, 2,000 GiB transfer, $12/month, with hourly billing and
   a monthly cap for the bundled plan. Optional backups, extra block storage,
   taxes, and overage costs are additional; $5 signup credit may partially
   offset eligible usage. No paid services have been approved.

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

## Actual server created (2026-11-07)

- DigitalOcean Droplet: `dcc-universal-dev`; IPv4 `104.236.113.110`; NYC3.
- Operating system: Ubuntu 24.04 LTS x64.
- Basic Regular SSD: 1vCPU, 2GB RAM, 50GB SSD, 2TB included transfer, $12/month ($0.018/hour) base charge.
- DigitalOcean free cloud firewall `dcc-universal-dev-inbound` attached: SSH TCP/22 only inbound; outbound ICMP/TCP/UDP allowed. DNS/TLS ports 80 and 443 must be opened before using Caddy publicly.
- Key-based SSH configured with owner-downloadable bootstrap key; no password login selected, no public or private key material in Git.
- No paid backups, block volumes, database service, reserved IP, or load balancer created. No application running publicly yet.
- The server accrues hosting charges until destroyed, regardless of whether the app is installed. Turning a Droplet off does not necessarily eliminate storage/compute charges.

