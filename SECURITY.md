# Security policy

## Reporting a vulnerability

Do not post vulnerability details, exploit steps, affected private code, credentials, or sensitive logs in public Issues, pull requests, or discussions. Those conversations are public.

Before publishing this repository, maintainers should enable GitHub **Private vulnerability reporting** in the repository's Security settings. When enabled, use **Report a vulnerability** on the Security page to send details privately to the maintainers.

If the private reporting button is unavailable, open an Issue containing no vulnerability details and ask the maintainers for a private contact method. Share sensitive details only after a private channel is available.

The actual reporting-channel and release-verification status is tracked in [the release acceptance record](docs/acceptance.md). The presence of this policy does not mean a private reporting channel has already been enabled.

## Scope and trust boundaries

This early candidate targets Windows and delegates through local Codex CLI processes to user-selected providers. Submitted prompts and relevant workspace content can leave the machine. Use providers you trust.

Role sandboxes, workspace path validation, environment-variable filtering and encrypted local credential storage reduce specific risks; they do not constitute a complete security boundary for every inherited tool, skill or local file. Review MCP/Skills permissions before allowing them in worker tasks. Parallel writing tasks share a workspace unless the caller explicitly separates them.

Do not attach full configuration, credential stores, task histories or raw diagnostic logs to public reports. Provide a minimal synthetic reproduction and redact secrets and private code. Supported environments and verification limits are documented in [compatibility](docs/compatibility.md).
