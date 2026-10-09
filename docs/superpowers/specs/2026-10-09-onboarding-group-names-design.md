# Onboarding Group Names and Model Query Errors

## Problem

The default provider entries have empty labels, so the settings panel renders blank group-name fields. The first custom provider is labeled `新分组 8` because its number is derived from all seven predefined providers. HTTP model-query failures are also surfaced as terse English status text, leaving users without a useful next step.

## Design

- Give each predefined provider a visible route-specific default label, including the default groups for Route A and Route B.
- Number custom groups independently within the selected route. The first custom group on either route is `新分组 1`; later custom groups use the next unused number on that route.
- Translate model-list HTTP errors into concise Chinese guidance. For HTTP 403, explain that the provider refused access, suggest checking key permissions/account or IP restrictions, and note that users can enter a model ID manually. Keep transport errors distinct from HTTP responses.

## Boundaries

- Keep provider IDs, key slots, and route behavior unchanged.
- Do not reveal response bodies that may contain provider or account details.
- Do not change the model-list request protocol or claim a 403 has one universal cause.

## Acceptance

- Default group-name fields are populated with route-specific labels.
- The first newly added group on a route is named `新分组 1`, regardless of predefined provider count.
- Adding more groups to a route uses unique, increasing available custom-group numbers; Route A and Route B number independently.
- HTTP 403 displays an actionable Chinese message, while TLS/URL errors remain clearly identified as connection failures.
