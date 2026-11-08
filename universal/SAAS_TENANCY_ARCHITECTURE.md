# DCC Universal — commercial multi-tenant workspace architecture

## Product objective

Build ONE commercially deployable application for MANY paying client companies.
Each company gets its own independent operations workspace, staff roles, customer
records, contractors, jobs, route plans, pricing policies, activity history,
external connectors, secrets and account settings.

**Company != connector.** A customer can combine several *authorized* data
sources **inside its own workspace**. Data never crosses customer boundaries.

This repository is an independently developed MVP; it is not yet safe or
complete for commercial onboarding or customers' sensitive data.

## Non-negotiable boundaries

- **Tenant:** one customer organization, identified internally by an immutable
  organization ID (future), with a human-readable slug for display.
- **Membership:** users only belong to their own tenant(s). Every request uses
  the tenant resolved from an authenticated, authorized membership. Never
  accept a caller-supplied tenant ID as the security authority.
- **Data:** each object carries tenant ownership: contractors, credentials,
  API sources, assets, work orders, routes, payments, events, attachments,
  caches and scheduled jobs. Queries and uniqueness constraints scope by tenant.
- **Credentials:** external service credentials belong to their customer's
  connection and are stored encrypted at rest with a separate secret-management
  key. They must never appear in customer-facing API replies, source control,
  analytics or log lines.
- **Operations:** background jobs, webhook handlers, imports, exports and
  downstream dispatch calls must resolve tenant identity from a validated
  per-tenant source configuration, not an arbitrary incoming payload.
- **No cross-tenant sharing:** no global contractor pool, route list, job
  search, deduplication by contact info, or transfer of a company's records.
  Shared product code and infrastructure do NOT imply shared customer data.
- **Destructive sync:** absent upstream records must NOT be removed by default;
  require explicit administrative policy and auditable confirmation.
- **Demo data:** fictional examples must be explicitly flagged, non-deliverable,
  and never used for live contractor notifications or routing.

## Flexible customer data model

**Normalized operational core + per-tenant schema mapping**. Each connected
system may use a different field nomenclature and status vocabulary:

| Canonical field | Customer A might use | Customer B might use |
| --- | --- | --- |
| contractor.external_id | Installer ID | Vendor Worker Number |
| contractor.name | Tech Full Name | Provider |
| contractor.status | Approved / Pending | Available / Training |
| contractor.address | Home Base | Dispatch From |
| work_order.external_id | Ticket # | Work Request ID |
| route.price | Offered Rate | Payout |

Every connector includes its own saved *field mapping*, *status mapping*,
identity key, optional custom fields, and synchronizing behavior.
Custom fields not used by the shared workflow should stay scoped to the
source/tenant; don't force every client to rename their upstream columns.

Missing or ambiguous critical fields must lead to a preview or actionable
mapping error — never fabricated coordinates or guessed identities.

## Integration gallery (target UX)

1. Company owner opens **Integrations**.
2. Picks CSV, secure REST API, or a supported vendor integration.
3. Authorizes access (OAuth when supported; scoped API key for generic REST).
4. Maps source fields to the DCC common model, including custom fields.
5. Previews new, changed, skipped and invalid records; resolves exceptions.
6. Explicitly approves the first import. Status and source provenance remain
   visible in the contractor roster.
7. Chooses approved refresh intervals; reviews sync history and failures.
8. Can disconnect or rotate credentials without deleting historical work.

A 'connected' badge must reflect a verified source test, not merely a saved
URL or a placeholder integration tile.

## Tenant isolation rollout

### Existing MVP foundations (do NOT confuse with complete SaaS)
- Organization-scoped routes, contractor roster and staff memberships.
- Source-scoped contractor IDs, audited import changes and demo fixtures.
- CSV import and deployer-configured read-only HTTPS JSON connector.
- Local SSH-tunnel preview only, no externally available signup or billing.

### Before first external paying customer
1. Create a tenant registry, immutable IDs, tenant lifecycle/ownership and
   repeatable customer provisioning. No self-signup until abuse/billing controls.
2. Use database-enforced tenant isolation, such as PostgreSQL RLS policies with
   per-request tenant context, and test bypass/resolution paths. Consider
   separate physical databases for customers needing stronger isolation.
3. Implement secure tenant-scoped connector management, encrypted secrets,
   allowlisted egress, OAuth where supported, schema mapping and optional
   custom fields; per-tenant API usage limits and durable sync logs.
4. Add companies' own staff invitations, RBAC, email verification, session
   termination, support access approvals and account recovery.
5. Audit every work-order, route, offer, attachment, price, payment and search
   endpoint for tenant boundaries; add two-tenant adversarial tests.
6. Configure HTTPS domain, production SMTP, monitoring, off-server backups,
   restore testing, incident logging, security patch policy, data retention,
   account export and deletion.
7. Add subscription billing, plan limits, trials, customer support tools and
   explicit privacy/terms/processing agreements.
8. Confirm independent ownership of source code and data rights before
   commercial release; exclude any employer-owned confidential content.

## Product principle

One application. One workspace per customer. Unlimited *types* of
authorized customer data through connectors — no mixing between companies.
