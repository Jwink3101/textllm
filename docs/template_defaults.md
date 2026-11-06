# Templates and Environment Variables

`TEXTLLM_TEMPLATE_FILE` selects the template used when textllm creates a new conversation, using a file path or `@name`. `--template` overrides it. Before writing the new conversation file, textllm fills a small set of placeholders from the current environment-backed defaults.

Default model and temperature also come from the environment when the conversation does not set them. In practice, this gives you two useful patterns:

- Omit `model` or `temperature` from the template to inherit the current environment default at call time.
- Put `{model}` or `{temperature}` in the template to write the current environment default into each new conversation file.

## Named Templates

Named templates are ordinary Markdown conversation templates. Their names have no built-in provider or model meaning; their contents define the settings and prompts.

Configure a directory, individual registrations, or both in an environment file:

```dotenv
TEXTLLM_TEMPLATE_DIR="${HOME}/.config/textllm/templates"
TEXTLLM_NAMED_TEMPLATE_openai="${HOME}/work/prompts/openai.md"
TEXTLLM_NAMED_TEMPLATE_special="${TEXTLLM_TEMPLATE_DIR}/custom/special.md"
TEXTLLM_TEMPLATE_FILE=@openai
```

```bash
textllm --template @openai
textllm --template @special "My Conversation.md"
textllm --template '@writing help'
textllm --template "./custom template.md"
textllm --list-templates
```

Directory discovery uses immediate files ending in lowercase `.md`, removing that final extension to form each name. For example, `writing help.md` defines `@writing help`. Dotfiles and directories are ignored; subdirectories are not searched. Symlinks to files are supported. No directory is searched unless `TEXTLLM_TEMPLATE_DIR` is configured.

Names are case-sensitive. Shell-compatible environment registrations should use names matching `[A-Za-z_][A-Za-z0-9_]*`; directory filenames can use spaces, hyphens, and other filename characters. Quote selectors and paths where needed. Named selectors cannot be empty, contain `/` or `\`, or be `.` or `..`.

### Selection and Precedence

Select a template using `--template`, then `TEXTLLM_TEMPLATE_FILE`, then the built-in template. For a selected `@name`, look up `TEXTLLM_NAMED_TEMPLATE_<name>` first, then `<name>.md` in `TEXTLLM_TEMPLATE_DIR`. An unknown name is an error. Explicit registrations win regardless of which environment file supplied the directory setting.

Registration values are file paths, not other `@name` selectors. Empty, missing, unreadable, or invalid UTF-8 registered files are errors when selected; textllm does not fall back to a directory entry. An empty `TEXTLLM_TEMPLATE_DIR` disables discovery. An invalid directory does not block directly selected paths, explicit registrations, or the built-in template.

Selectors beginning with `@` always mean names, even if a matching literal file exists. Use `--template ./@openai` to select a file literally named `@openai`.

Templates only initialize new conversations. Existing conversations ignore template selection. Changes to a template do not alter conversations already created from it. Lookup and template-read failures occur before creating a new conversation file.

### Environment Files and Paths

Textllm starts with the process environment, then loads `TEXTLLM_ENV_PATH`, the discovered `.env`, and explicit `--env`, in that order. Later files override earlier settings. All template lookup uses the resulting environment.

Python-dotenv expands `${VAR}` references in environment files, including `${HOME}` and `${TEXTLLM_TEMPLATE_DIR}`. Bare `$VAR` is not expanded. Define referenced variables before using them. Expansion happens as each file loads; changing a directory variable later does not retroactively update an earlier expanded path. Undefined references can become empty strings. See the [python-dotenv expansion documentation](https://github.com/theskumar/python-dotenv#variable-expansion).

Relative paths are relative to the invocation's working directory, not the environment file. Registration paths are not implicitly relative to `TEXTLLM_TEMPLATE_DIR`. Use `${HOME}` rather than relying on tilde expansion in environment files. Textllm does not perform additional shell evaluation or environment expansion on paths.

For shell configuration such as `.bashrc`, export registrations so child processes can see them:

```bash
export TEXTLLM_TEMPLATE_DIR="$HOME/.config/textllm/templates"
export TEXTLLM_NAMED_TEMPLATE_special="$HOME/work/custom template.md"
```

### Listing and Validation

`--list-templates` loads the environment, lists effective names alphabetically with paths and sources, and exits. It also shows directory entries shadowed by explicit registrations. Names and paths use quoted, escaped strings so whitespace remains unambiguous.

Listing validates effective template files, reports broken entries without hiding the remaining entries, and returns a nonzero status for broken registrations, unreadable files, or directory discovery errors. With no configured templates, it prints configuration guidance and succeeds. It does not create a conversation, read stdin, launch an editor, or call a model, and needs no provider credentials.

## Inherit the Default Model

Set the defaults in the environment or an env file:

```dotenv
TEXTLLM_DEFAULT_MODEL=openai/gpt-4o-mini
TEXTLLM_DEFAULT_TEMPERATURE=0.2
TEXTLLM_DEFAULT_HEADER_LEVEL=2
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

`TEXTLLM_DEFAULT_HEADER_LEVEL` controls newly appended role markers unless the conversation sets `[textllm].header_level` or the invocation uses `--header-level`. It also controls initial markers in the built-in template. In the explicit template above, the initial markers stay plain because they do not use `{header}`. To apply the environment default to those markers too, use the pattern below.

## Control Role Markers in a Template

Set `TEXTLLM_DEFAULT_HEADER_LEVEL` in your shell or env file to choose the default Markdown heading level (`0` through `6`; `0` means plain separators):

```dotenv
TEXTLLM_DEFAULT_HEADER_LEVEL=2
```

The built-in template applies this default to its initial `System` and `User` markers. For a custom template, add the `{header}` placeholder immediately before each separator:

````markdown
# !!AUTO TITLE!!

{header}--- System ---

You are a concise assistant.

{header}--- User ---

````

With `TEXTLLM_DEFAULT_HEADER_LEVEL=2`, this template creates `## --- System ---` and `## --- User ---`. `{header}` expands to the appropriate hashes and trailing space, such as `## `, or to an empty string for level `0`. Its precedence is `--header-level`, the template's `[textllm].header_level`, `$TEXTLLM_DEFAULT_HEADER_LEVEL`, then `0`. To fix the level for a particular template, add `[textllm]` with `header_level = 2` to its TOML settings block. Omit `{header}` when the explicit template should preserve its separators exactly.

## Fill the Default Model into the File

Use placeholders when you want each new conversation to record the default model and temperature selected by the current shell or env file:

````markdown
# {AUTO_TITLE}

```toml
model = "{model}"
temperature = {temperature}
```

Created with {version} at {now}

{header}--- System ---

You are a concise code reviewer. Focus on correctness, regressions, and missing tests.

{header}--- User ---

````

The supported placeholders are `{AUTO_TITLE}`, `{header}`, `{model}`, `{temperature}`, `{now}`, and `{version}`. `{model!r}` is also supported if you want Python-style quoting like the built-in template uses.

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
