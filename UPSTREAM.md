# Upstream

This private repository is based on the official Google Ads MCP server:

- Upstream: https://github.com/googleads/google-ads-mcp
- Imported branch: `main`
- Imported commit: `ba47210245f2925a130a2770a4d272d5dd0c91cd`
- License: Apache License 2.0

## Updating from upstream

```bash
git remote add upstream https://github.com/googleads/google-ads-mcp.git
git fetch upstream
git merge upstream/main
```

## Local direction

The initial code remains read-only. Planned local extensions should be deliberately narrow:

- resolve Google Ads attribution from GCLID and GAQL data;
- validate offline conversion payloads;
- upload qualified-lead and closed-won conversions;
- support conversion restatement and retraction;
- require dry-run/validation, idempotency, allowlists, and audit logging;
- do not expose campaign or budget mutation tools by default.
