# Template Defaults and Environment Variables

`TEXTLLM_TEMPLATE_FILE` sets the Markdown file used when textllm creates a new conversation. Before writing the new conversation file, textllm fills a small set of placeholders from the current environment-backed defaults.

Default model and temperature also come from the environment when the conversation does not set them. In practice, this gives you two useful patterns:

- Omit `model` or `temperature` from the template to inherit the current environment default at call time.
- Put `{model}` or `{temperature}` in the template to write the current environment default into each new conversation file.

## Inherit the Default Model

Set the defaults in the environment or an env file:

```dotenv
TEXTLLM_DEFAULT_MODEL=openai/gpt-4o-mini
TEXTLLM_DEFAULT_TEMPERATURE=0.2
TEXTLLM_TEMPLATE_FILE=templates/code-review.md
```

Then make the template omit those settings:

````markdown
# !!AUTO TITLE!!

```toml
# model and temperature are inherited from the environment defaults.
```

--- System ---

You are a concise code reviewer. Focus on correctness, regressions, and missing tests.

--- User ---

````

When a new conversation is created from this template, the conversation file will not contain `model` or `temperature`. At call time, textllm merges the built-in defaults underneath the conversation settings, so `TEXTLLM_DEFAULT_MODEL` and `TEXTLLM_DEFAULT_TEMPERATURE` are used.

## Fill the Default Model into the File

Use placeholders when you want each new conversation to record the default model and temperature selected by the current shell or env file:

````markdown
# {AUTO_TITLE}

```toml
model = "{model}"
temperature = {temperature}
```

Created with {version} at {now}

--- System ---

You are a concise code reviewer. Focus on correctness, regressions, and missing tests.

--- User ---

````

The supported placeholders are `{AUTO_TITLE}`, `{model}`, `{temperature}`, `{now}`, and `{version}`. `{model!r}` is also supported if you want Python-style quoting like the built-in template uses.

## Override the Default Model

If the template includes a setting, it overrides the environment default for conversations created from that template:

````markdown
# !!AUTO TITLE!!

```toml
model = "openai/gpt-5.5"
temperature = 1.0
```

--- System ---

You are an expert assistant. Provide concise, accurate answers.

--- User ---

````

Use this when the template is tied to a specific provider or model. Otherwise, omit `model` so the same template can follow whichever default model is configured for the current shell, `.env`, `TEXTLLM_ENV_PATH`, or `--env` file.

## Unsupported Placeholders

Only the placeholders listed above are filled. Unknown placeholders are left as written:

````markdown
```toml
metadata = "{project_name}"
```
````

This keeps custom templates from failing when prompts include ordinary braces, JSON examples, or other text that is not meant for textllm.
