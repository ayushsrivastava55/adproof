# External Integration Rules

- Verify current official documentation before implementing or changing provider behavior.
- Do not invent SDK methods, fields, statuses, limits, or guarantees.
- Model external operations as asynchronous when the provider does.
- Persist provider references and normalized statuses.
- Make retries bounded and idempotent.
- Expose terminal failures clearly.
- Never expose provider credentials to clients.
