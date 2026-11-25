# Migrating Settings to `[llm]`

Provider request settings now belong under `[llm]`. Application settings remain under `[textllm]`, including `[textllm.title]`. Existing conversations and templates continue working with a deprecation warning; no removal release has been scheduled. Files are never migrated automatically.

## Before and After

Before:

```toml
model = "openai/gpt-6-luna"
max_output_tokens = 16000

[reasoning]
effort = "high"

[textllm]
backend = "responses"

[textllm.title]
reasoning = { effort = "low" }
```

After:

```toml
[llm]
model = "openai/gpt-6-luna"
max_output_tokens = 16000

[llm.reasoning]
effort = "high"

[textllm]
backend = "responses"

[textllm.title]
reasoning = { effort = "low" }
```

Move every provider setting, including `model`, inside `[llm]`. Prefix provider table names: `[reasoning]` becomes `[llm.reasoning]`, and `[[tools]]` becomes `[[llm.tools]]`. Inline arrays and tables need no changes beyond being placed under `[llm]`. Keep application tables outside `[llm]`; putting `[textllm]` inside it is an error. `llm` itself must be a table.

TOML assignments belong to the most recent table header. Place `[llm]` before provider assignments, rather than adding it above an existing `[textllm]` header and assuming subsequent assignments have moved.

## Compatibility and Precedence

Legacy root-level provider keys are recursively merged with `[llm]`, with `[llm]` winning conflicts within the same source. Arrays and scalar values replace earlier values. Unknown root-level keys still count as legacy provider settings; future local features must reserve their application tables explicitly. Root-level `tools` retains its provider meaning.

Saved settings are normalized before invocation overrides. Each override fragment is normalized separately, then merged in command-line order. Dedicated CLI flags retain precedence. Both old and new override syntax work, but prefer:

```bash
textllm -c -o 'llm.temperature = 0.3'
textllm -c -o 'llm.reasoning.effort = "high"'
textllm -c -o 'textllm.title.model = "openai/gpt-6-luna"'
```

One warning per invocation reports legacy settings from the file or overrides, using normal stderr logging and verbosity controls. Repeated chat/title requests do not repeat the warning. Omitted model and temperature retain their existing behavior: the model falls back to the environment default; temperature is sent only when explicitly configured.

## Templates and Existing Conversations

The built-in template and repository examples use `[llm]`. For personal templates, check `TEXTLLM_TEMPLATE_FILE`, `TEXTLLM_TEMPLATE_DIR`, and every `TEXTLLM_NAMED_TEMPLATE_*` registration, including values loaded from environment files. `textllm --list-templates --list-templates` lists named paths; separately check any default template selected by a literal path.

Back up each unique template file and edit only its settings block. Preserve comments, `{model}`, `{temperature}`, `{header}`, and double-brace escapes. Update nested provider headers as described above. Multiple registrations may point to the same file.

Updating a template affects new conversations only. Existing conversations can be migrated when convenient or kept in the legacy format. Invocation overrides never rewrite saved TOML.

For older dependency and model-name changes, see [Earlier Backend Migrations](migration_backends.md).


## Maintainer Checklist for Removing Legacy Support

This is future work, not authorization to remove compatibility now. No removal release has been scheduled.

1. Choose a removal release and announce it in the changelog and this guide before removing support. Keep the migration examples available afterward so users of old conversations can recover.
2. In `textllm/config.py`, change `_normalize_settings()` to reject every root-level key outside `llm` and `_LOCAL_SETTINGS_TABLES`, with an actionable error directing users to this guide. Remove the legacy/provider merge. Keep namespace validation, independent copies, local-table handling, and support for empty settings or `[textllm]`-only files; an explicit `[llm]` table is not required when provider settings are omitted.
3. Remove `_has_legacy_settings()` and `_LEGACY_SETTINGS_WARNING` from `textllm/config.py`, their imports and warning branch in `Conversation.settings`, and `_legacy_overrides` tracking from `Conversation.__init__()` and `textllm/cli.py`. Keep per-fragment normalization and recursive override merging so later overrides still win. Keep provider-table unpacking and local-table exclusion from requests.
4. Convert legacy fixtures, including the shared `config.TEMPLATE` in `test_textllm.py`, to `[llm]`. Replace compatibility, mixed-format precedence, and warning-frequency tests with rejection tests for legacy files, templates, direct `Conversation` overrides, and `-o` fragments. Cover root-level scalars, nested tables, tool arrays, and mixed old/new formats; reject legacy keys even when an explicit `[llm]` value would previously have won.
5. Verify invalid templates and override fragments fail before conversation creation or modification. Keep deterministic coverage for new-format override ordering, dedicated flag precedence, all three backends, title overrides and tool isolation, omitted model/temperature behavior, and unchanged saved TOML. Do not migrate personal files in tests or silently rewrite existing conversations.
6. Update the README, current architecture/format documentation, this guide, the changelog, `AGENTS.md`, and `docs/testing_strategy.md` to describe rejection rather than deprecation. Audit the built-in and example templates for legacy syntax. Historical before/after examples may remain clearly labelled.
7. Run `pytest test_textllm.py --cov --cov-report term --cov-report html` and `git diff --check`. Search for `_has_legacy_settings`, `_LEGACY_SETTINGS_WARNING`, and `_legacy_overrides` to confirm obsolete implementation references are gone.

Continue reserving application table names explicitly. Removing legacy support does not by itself authorize reusing provider names such as `tools` for a new application feature.
