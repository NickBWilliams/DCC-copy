# Contractor sources: nationwide demo roster and customer-authorized integrations

The single DCC Universal workspace can aggregate contractors from multiple
external systems without creating a separate account for every worker.

## Load 100 safe synthetic workers

1. Update the private preview to the latest independent personal GitHub branch.
2. Sign in at http://localhost:8000 through your SSH tunnel.
3. Open **Contractor roster** → **Load 100 demo contractors**.
4. The action creates exactly two fictitious test-only contractors in each
   of the 50 US states. Repeating it will NOT duplicate them.
5. Each has an ID like \`DEMO-IL-01\`, a clearly labeled fake address and an
   approximate coordinate near a US city. Statuses and service radii vary.
6. **Remove demo contractors only** deletes only the flagged test records.

**Important:** The demo street addresses are intentionally fictional and do
not correspond to verified physical locations. Their coordinates simulate
regional proximity but do not verify an address. Do not use them to mail,
pay, hire, route real work, or contact actual people. The fixtures contain
no email addresses.

## First real integration — authorized CSV import

The CSV import UI is available immediately in **Contractor roster**.
Required columns are \`external_id,name,latitude,longitude\`; optional
columns are \`email,status,home_address,service_radius_miles\`.
Common aliases (ID, Full Name, Lat, Lon) are recognized. Geo coordinates
must be supplied for now; unknown or missing coordinates are rejected rather
than fabricated. Statuses must be \`active\`, \`in_training\`, or \`inactive\`.

Choose a source key (for example \`workforce-db\`), upload the company's
authorized CSV export, select **Preview changes**, then **Apply imported
records**.

- Each imported contractor gets a source-qualified durable ID, e.g.
  \`workforce-db:contractor_101\`.
- A second company or a second connected source can use the same original
  external ID without collisions. Data is organization-scoped.
- Re-import by source + external ID updates records in place with version
  increments and audit entries. Unchanged records are skipped.
- Missing contractor IDs are **never automatically deleted or deactivated**.
  This protects routes and assignments during transient upstream omissions.
- A manual contractor record is never silently claimed by an import source.
- Preview does not modify any data; only admins/owners can import.
- Test fixtures are visibly marked **DEMO ONLY** and remain separate.

Never import a company's contractor base without authorized access and an
appropriate data-handling agreement. Do not import employer records into a
personal commercial prototype without permission.

## Second integration — server-configured HTTPS JSON API

The app also supports a *real read-only pull adapter*. For each organization,
an administrator configures an approved HTTPS JSON API URL, bearer-token
environment variable, field mapping and optional external status mapping.
No credentials are sent to the browser, stored in Git or accepted as
arbitrary HTTP URLs in a user request.

Example: in the server's untracked environment, configure
\`DCC_CONTRACTOR_CONNECTORS_JSON\` to the following JSON (replace placeholders):

\`\`\`json
{
  "personal": {
    "your-company-crm": {
      "url": "https://api.your-company-example.com/v1/contractors",
      "token_env": "DCC_CONNECTOR_COMPANY_CRM_TOKEN",
      "records_path": "data.contractors",
      "mapping": {
        "external_id": "id",
        "name": "fullName",
        "email": "email",
        "status": "status",
        "home_address": "address.full",
        "latitude": "location.latitude",
        "longitude": "location.longitude",
        "service_radius_miles": "radius"
      },
      "status_map": {
        "approved": "active",
        "pending": "in_training",
        "disabled": "inactive"
      }
    }
  }
}
\`\`\`

Also set \`DCC_CONNECTOR_COMPANY_CRM_TOKEN\` **only on the private server**.
The organization key must match the currently signed-in company. Choose
the provider URL and mapping according to the customer's published API
and explicitly granted read permissions.

Then, under **Contractor roster → Company API sources**, the owner can
**Preview API pull** or **Pull now**. Both requests use the source
configuration on the server; the API pull is read-only externally and the
sync applies changes only inside that tenant's DCC roster.

### Current adapter limits

- HTTPS, port 443 only, public DNS hostname. No redirects, embedded
  credentials, query-string tokens, arbitrary browser-supplied URLs,
  localhost or private IP endpoints. The DNS check is defense-in-depth and
  is not equivalent to strict network egress isolation. Trusted server
  administrators must configure all sources.
- Bearer-token authentication, JSON response, up to 2,000 contractor
  records and 2 MB per response. Does not implement pagination yet.
- One-time/manual pull; scheduling, change webhooks, incremental cursor sync,
  rate limits and OAuth connector flows are roadmap items.
- No address geocoding in bulk import yet. Normalization requires coordinates.
- No automatic upstream writes; importing into the app does not change the
  customer's system.
- No connector to a named vendor is active by default: it must be explicitly
  configured for each customer and their supported API.

## Recommended next integrations

Implement specific vendor adapters (for example, monday.com, Salesforce,
Airtable, Google Sheets or a proprietary CRM) behind this shared normalized
contractor model, with native OAuth/API scopes, field mapping, incremental
sync, event history, conflict resolution, and configurable refresh schedule.
Contractor records should be related to companies and sources, not globally
merged by name, phone or email.
