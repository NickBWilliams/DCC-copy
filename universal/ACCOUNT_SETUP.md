# Independent DCC Universal — account inventory
Updated 2026-11-07

**Personal owner address:** nicholas.b.williams@outlook.com.
**Rule:** never reuse Terraboost keys, accounts, hosts, billing or datasets.
This file inventories accounts; it does not claim third-party registrations occurred.

| Service | Account ownership | Status | Next action |
| --- | --- | --- | --- |
| GitHub | Personal (NickBWilliams) | Already moved by owner | Keep DCC-copy private until IP rights reviewed; use feature/dcc-universal-foundation |
| HeiGIT / openrouteservice | Personal Outlook | **User confirms account created; API key not connected or validated** | Sign in to account.heigit.org through a private browser profile, verify the account email, and configure HEIGIT_API_KEY only in the private deployment environment |
| US Census Geocoder | Public API | No account needed | Already integrated for US addresses |
| PostgreSQL | Dedicated Docker container | Self-hosted configuration committed | No separate external database-provider account needed |
| Hosting | Personal account | Not selected / not purchased | Select a VPS provider, account email = Outlook |
| Domain / DNS | Personal account | Not selected | Register after product naming is final |
| Transactional email (SMTP) | Personal account | Not selected / not created | Select after deciding production volume; wire private SMTP credentials |
| SMS | Optional per company | Not selected / not created | Do not purchase until requested |
| OSRM | Dedicated host, optional | Not provisioned | Needed for commercial self-hosted routing at scale; not public demo |
| Map rendering / tiles | Optional, later | Not selected | MapLibre browser component can use licensed/hosted tiles when needed |
| Payment processor | Not needed for MVP | Not selected | Defer until subscription billing |

## HeiGIT specifics

Current API hostname: https://api.heigit.org/ — not the deprecated
api.openrouteservice.org URL. Recommended call paths:

- Routing directions: /openrouteservice/v2/directions/driving-car
- Fleet optimization (VROOM): /vroom/v0

HeiGIT's Standard plan supports commercial use, **subject to its terms** and
CC BY-SA licensed results. Review attribution, data storage/caching, quota,
privacy, and customer usage before launching a paid SaaS. Do not embed the
API key in client-side JavaScript; this repository keeps it server-side.

## First deployment prerequisites

- A personal domain, HTTPS certificate and private DNS control.
- Personal server or VPS, with private PostgreSQL storage and backups.
- SMTP provider configured to deliver staff magic links.
- HeiGIT account + API key, or a properly licensed private OSRM deployment.
- A non-development DCC_ENV=production setting and all secrets outside Git.
- Automated tests passing, threat model and legal/IP review completed.

**Never place private tokens or passwords in GitHub commits, emails, or chat.**

## Personal routing account checkpoint (2026-11-07)

Owner reports the HeiGIT account has been created. No saved authenticated browser session is available to the assistant yet, so the account identity, usable API key, and live Directions/VROOM calls have **not** been verified. Do not place API keys into GitHub commits, issues, chats, or frontend HTML. After a secure sign-in profile is explicitly connected, validate the active key and provider quota without displaying the key.
