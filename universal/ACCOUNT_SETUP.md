# Independent DCC Universal — account inventory
Updated 2026-11-07

**Primary personal owner:** nicholas.byron.williams@gmail.com (verified GitHub and HeiGIT).  
**Secondary approved personal email:** nicholas.b.williams@outlook.com.
**Rule:** never reuse Terraboost keys, accounts, hosts, billing or datasets.
This file inventories accounts; it does not claim third-party registrations occurred.

| Service | Account ownership | Status | Next action |
| --- | --- | --- | --- |
| GitHub | NickBWilliams, personal Gmail | Verified connected account and email | Keep independent DCC Universal branch for review; do not merge until IP/code provenance reviewed |
| HeiGIT / openrouteservice | Personal Gmail | Authenticated account and existing Basic Key confirmed; Directions V2 quota 2,000 and 40/min shown; no VROOM quota visible | Add secret HEIGIT_API_KEY to private runtime at deployment; default DCC_HEIGIT_USE_VROOM=false |
| US Census Geocoder | Public API | No account needed | Already integrated for US addresses |
| PostgreSQL | Dedicated Docker container | Self-hosted configuration committed | No separate external database-provider account needed |
| Hosting | Personal Gmail or Outlook | Not purchased or configured | Select independent VPS hosting once deployment hardening is complete |
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

## Account verification checkpoint (2026-11-07)

- Authenticated GitHub profile: NickBWilliams, email nicholas.byron.williams@gmail.com.
- Authenticated HeiGIT account: same personal Gmail account.
- A Basic Key exists, created November 7, 2026; Directions V2 quota is shown. Neither its full value nor any credentials were copied to code, GitHub, or chat.
- VROOM Optimization quota is **not visible** on the account page. DCC Universal defaults to Directions-only routing using a geographic 2-opt sequence and real road distance measurements; VROOM is optional.
- Existing HeiGIT key has **not** been installed on a production server or validated through a live API call from DCC Universal. Hosting not yet selected.
- Both personal email addresses are approved for new services. No requirement that all third-party services share one email.

Do not share passwords or API keys in public GitHub files, frontend code, or chat.
