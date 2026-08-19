# Google Ads MCP

Hosted MCP server for Google Ads reporting, GCLID attribution, and guarded
offline conversion uploads. Reporting uses the Google Ads REST API; offline
conversion ingestion uses the Data Manager API. Both reuse one service-account
key, and Auth0 protects the public Streamable HTTP endpoint.

## Tools

- `list_accessible_customers`: list accounts directly accessible to the service account.
- `list_linked_accounts`: list advertising accounts directly linked beneath an MCC.
- `get_customer_details`: account, currency, timezone, and conversion setup.
- `get_campaign_performance`: campaign traffic, cost, and conversion metrics.
- `get_search_terms`: actual paid-search queries with campaign/ad-group metrics.
- `lookup_gclid`: map one CRM GCLID and click date to campaign/ad-group/ad context.
- `list_conversion_actions`: return exact conversion action resource names.
- `upload_offline_conversion`: validate or submit one GCLID/GBRAID/WBRAID conversion.
- `get_offline_conversion_upload_status`: retrieve asynchronous upload diagnostics.

The upload tool is validation-only by default. A real write requires both
`validate_only=false` and `confirm_write=true`. A successful real submission is
asynchronous and must be checked by `request_id` before it is considered complete.

## Why this is separate from Analytics and Search Console

Google Ads reporting requires a **developer token** in addition to OAuth
credentials. Data Manager ingestion does not use the developer token. The same
Google Cloud project, service account, and JSON key are reused for both APIs.

Required Google Ads setup:

1. Use or create a Google Ads manager account.
2. Obtain a developer token from the manager account's API Center.
3. Enable both the Google Ads API and Data Manager API in the Cloud project.
4. In Google Ads, open **Admin > Access and security**, add the service-account
   email as a user, and grant the minimum access needed for reporting/uploads.
5. If accounts are reached through a manager, set its 10-digit ID as
   `GOOGLE_ADS_LOGIN_CUSTOMER_ID`.

## CRM conversion model

Do not send unqualified leads as negative conversions. Prefer distinct actions
and values, for example:

- Lead submitted: low-value or secondary action.
- Qualified opportunity: primary action with a higher value.
- Won deal: highest-value action using actual or modeled revenue.

The upload tool converts the conversion action's exact `resource_name`, returned
by `list_conversion_actions`, into the Data Manager `productDestinationId`; it
does not rely on matching a free-form name. The action must belong to the
operating `customer_id`. Always supply a unique, stable CRM opportunity ID as
`order_id`.

Data Manager mapping:

- `customer_id` -> `destination.operatingAccount.accountId`
- `GOOGLE_ADS_LOGIN_CUSTOMER_ID` -> `destination.loginAccount.accountId`
- conversion action ID -> `destination.productDestinationId`
- GCLID/GBRAID/WBRAID -> `event.adIdentifiers`
- conversion timestamp -> `event.eventTimestamp`
- `order_id` -> `event.transactionId`

## Deployment variables

```text
GOOGLE_APPLICATION_CREDENTIALS_BASE64=BASE64_SERVICE_ACCOUNT_JSON
GOOGLE_ADS_DEVELOPER_TOKEN=SECRET_FROM_ADS_API_CENTER
GOOGLE_ADS_LOGIN_CUSTOMER_ID=7646139372
GOOGLE_ADS_API_VERSION=v25

AUTH0_DOMAIN=YOUR-TENANT.us.auth0.com
AUTH0_AUDIENCE=https://YOUR-GOOGLE-ADS-MCP-DOMAIN
AUTH0_SCOPE=ads:manage
MCP_PUBLIC_URL=https://YOUR-GOOGLE-ADS-MCP-DOMAIN
```

`AUTH0_AUDIENCE` and `MCP_PUBLIC_URL` must not include `/mcp` or a trailing
slash. `GOOGLE_ADS_DEVELOPER_TOKEN` and the service-account JSON are secrets and
must never be committed or written to logs.

The endpoints are:

```text
Health: https://YOUR-DOMAIN/health
MCP:    https://YOUR-DOMAIN/mcp
OAuth:  https://YOUR-DOMAIN/.well-known/oauth-protected-resource
```

Never set `ALLOW_UNAUTHENTICATED_MCP=true` on a persistent public deployment.

Project-specific non-secret settings:

```text
Google Cloud project: learned-tube-493410-c2
Service account: analytics-mcp@learned-tube-493410-c2.iam.gserviceaccount.com
Google Ads manager Customer ID: 7646139372
Google Ads operating Customer ID: 2810392235
Offline conversion action ID: 7671182819
```

## GCLID limitations

Google Ads `click_view` requires a query filtered to exactly one day and only
supports dates from the last 90 days. Store both the GCLID and the original
click timestamp/date in CRM. Also retain UTMs as a durable secondary attribution
source.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .[dev]

GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
GOOGLE_ADS_DEVELOPER_TOKEN=replace-me \
MCP_AUTH_TOKEN=replace-me \
python -m google_ads_mcp.remote_server
```

Run checks:

```bash
black --check .
python -m unittest discover --buffer -s tests -p '*_test.py'
```

## Security notes

- Service-account JSON and developer tokens must never be committed.
- The container runs as a non-root user.
- Auth0 tokens are verified for RS256 signature, issuer, audience, expiry, and
  `ads:manage` permission.
- A real conversion upload requires an explicit two-flag confirmation.
- Data Manager validation-only requests never write data. Real submissions are
  asynchronous and must be verified through request diagnostics.
- The server does not expose campaign, bid, budget, keyword, or ad mutation tools.

## License

Apache License 2.0.
