# Independent DCC Universal — account inventory
Updated 2026-11-07

**Personal owner address:** nicholas.b.williams@outlook.com.
**Rule:** never reuse Terraboost keys, accounts, hosts, billing or datasets.
This file inventories accounts; it does not claim third-party registrations occurred.

| Service | Account ownership | Status | Next action |
| --- | --- | --- | --- |
| GitHub | Personal (NickBWilliams) | Already moved by owner | Keep DCC-copy private until IP rights reviewed; use feature/dcc-universal-foundation |
| HeiGIT / openrouteservice | Personal Outlook | **Not registered or verified here** | Register at https://account.heigit.org/ ; receive the Standard key; configure HEIGIT_API_KEY privately |
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
