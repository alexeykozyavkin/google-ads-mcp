# Railway read-only POC

This guide deploys the private Google Ads MCP server to Railway and connects it
to ChatGPT in developer mode. The POC exposes only the upstream read-only tools:

- `customers_list_accessible_customers`
- `search_search`
- `metadata_get_resource_metadata`

The Google Ads OAuth scope itself is not read-only. Read-only behavior is
enforced by the MCP tool surface: this POC contains no mutate or conversion
upload tools.

## Architecture

```text
ChatGPT
  -> HTTPS streamable MCP at /mcp
  -> FastMCP OAuth proxy
  -> Google OAuth consent
  -> Google Ads API
```

For the first POC, Auth0 is not in the request path. The upstream project
already uses FastMCP's Google provider, which proxies the Google OAuth flow and
requests the `https://www.googleapis.com/auth/adwords` scope directly.

Useful references:

- [OpenAI MCP authentication requirements](https://developers.openai.com/plugins/build/auth)
- [Connect and test an MCP server in ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [FastMCP OIDC proxy and callback configuration](https://fastmcp.mintlify.app/servers/auth/oidc-proxy)
- [Google Ads developer tokens](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)
- [Google Ads OAuth overview](https://developers.google.com/google-ads/api/docs/oauth/overview)

## 1. Collect the Google Ads identifiers

From the Google Ads manager account, record:

1. The manager customer ID (MCC), digits only.
2. The target client customer ID, digits only.
3. The developer token from the
   [Google Ads API Center](https://ads.google.com/aw/apicenter).
4. The token access level.

A developer token is required even though end-user account access is granted
through OAuth. Explorer, Basic, or Standard access can query production
accounts; Test Account access can query only Google Ads test accounts.

Never commit or paste the developer token into issues, pull requests, logs, or
documentation.

## 2. Configure the Google Cloud project

1. Select or create the Google Cloud project used for this MCP.
2. Enable the Google Ads API.
3. Configure the OAuth consent screen and allow the Google user who will test
   the connection.
4. Create an OAuth 2.0 client of type **Web application**.
5. Add the following authorized redirect URI:

   ```text
   https://<railway-domain>/auth/callback
   ```

6. Save the client ID and client secret in a password manager.

The redirect URI above is Google's callback to the FastMCP server. It is not a
ChatGPT callback URL.

## 3. Create the Railway service

1. Create a Railway service from the private
   `alexeykozyavkin/google-ads-mcp` repository.
2. Let Railway build the existing `Dockerfile`.
3. Generate a public HTTPS domain for the service.
4. Attach a persistent volume mounted at `/data`.

The volume stores encrypted OAuth client registrations and upstream Google
tokens. The file-based store is suitable for a single-instance POC. Use Redis
or another shared backend before running multiple replicas.

## 4. Configure Railway variables

Copy the names from `.env.example` into Railway. Required values:

| Variable | Value |
| --- | --- |
| `GOOGLE_PROJECT_ID` | Google Cloud project ID |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads developer token |
| `GOOGLE_ADS_MCP_OAUTH_CLIENT_ID` | Google OAuth Web client ID |
| `GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET` | Google OAuth Web client secret |
| `GOOGLE_ADS_MCP_BASE_URL` | Public Railway origin, HTTPS, no trailing slash |
| `GOOGLE_ADS_MCP_JWT_SIGNING_KEY` | Long random secret |
| `GOOGLE_ADS_MCP_STORAGE_ENCRYPTION_KEY` | A different long random secret |
| `GOOGLE_ADS_MCP_STORAGE_TYPE` | `filetree` |
| `GOOGLE_ADS_MCP_STORAGE_PATH` | `/data/oauth` |

Set `GOOGLE_ADS_LOGIN_CUSTOMER_ID` to the manager customer ID when the target
account is reached through that manager. Leave it empty only when it is not
needed.

Generate two independent secrets locally, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Railway supplies `PORT`; the application defaults to port 8080 if it is absent.

After setting the variables, redeploy the service.

## 5. Connect ChatGPT

The server must be reachable at:

```text
https://<railway-domain>/mcp
```

In ChatGPT:

1. Open **Settings**.
2. Select **Security and login**.
3. Enable **Developer mode**.
4. Open the Plugins page and add a new MCP connection.
5. Enter the full URL including `/mcp`.
6. Complete Google authorization and review the three discovered tools.

OpenAI requires a public HTTPS streamable HTTP endpoint, normally at `/mcp`,
and working OAuth discovery for account-specific data.

## 6. Smoke-test the read-only POC

Run these prompts in a new ChatGPT conversation:

1. “List the Google Ads customers I can access.”
2. “For customer `<customer-id>`, list active campaigns with their IDs and
   names.”
3. “For customer `<customer-id>`, summarize spend, clicks, and conversions
   over the last seven complete days.”

Expected result: ChatGPT calls only the customer, metadata, and search tools.
There must be no tools for changing campaigns, budgets, ads, or conversions.

## 7. Test CRM attribution by GCLID

For a CRM record with a GCLID and visit date, query the `click_view` resource.
Google requires `click_view` queries to cover exactly one day and makes the
data available for up to 90 days.

Before the first query, use `metadata_get_resource_metadata` for
`click_view` to confirm the compatible v25 fields. A representative query
shape is:

```text
resource: click_view
fields:
  - click_view.gclid
  - segments.date
  - campaign.id
  - campaign.name
  - ad_group.id
  - ad_group.name
conditions:
  - segments.date = '2026-08-06'
  - click_view.gclid = '<gclid-from-crm>'
limit: 10
```

Reference: [Google Ads API v25 click_view](https://developers.google.com/google-ads/api/fields/v25/click_view).

A successful result proves the first half of the target workflow:

```text
CRM opportunity -> GCLID + click date -> Google Ads campaign/ad group
```

Offline conversion uploads remain out of scope until this read-only flow is
stable and repeatable.

## Troubleshooting checklist

- **Container starts in stdio mode:** both OAuth client variables must be set.
- **Redirect URI mismatch:** Google Cloud must contain the exact
  `<base-url>/auth/callback` URI.
- **OAuth works but Ads calls fail:** verify the developer token access level,
  target customer ID, and optional manager login customer ID.
- **Users must reconnect after each restart:** confirm the Railway volume is
  mounted at `/data` and both signing/encryption secrets are stable.
- **Production account rejected:** a Test Account developer token cannot query
  production accounts.
- **ChatGPT cannot discover tools:** confirm the public URL includes `/mcp`
  and refresh the MCP connection after redeployment.
