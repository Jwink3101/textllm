# Named Templates Design

## Intent

This document describes the implemented named-template design. Usage and configuration examples are in [Template Defaults and Environment Variables](../template_defaults.md).

Named templates add convenient names for ordinary Markdown template files while preserving the existing file-based workflow. Users can register individual files through environment variables, discover templates in a configured directory, or combine both approaches. No separate registry file or new template format is needed.

## Command-Line Interface

```bash
textllm --template @openai
textllm --template @special "My Conversation.md"
textllm --template "./templates/custom template.md"
textllm --list-templates
```

A template selector beginning with `@` always refers to a named template. Every other selector is a file path, retaining the existing interpretation of `--template`.

For a file literally named `@openai`, use `--template ./@openai`. Named lookup never depends on whether a same-named file happens to exist in the working directory. An unknown name is an error, not a request to try a literal path.

`@` is preferable to `#` because an unquoted `#openai` starts a comment in Bash.

## Configuration

Three environment settings have distinct purposes:

| Setting | Purpose |
| --- | --- |
| `TEXTLLM_TEMPLATE_FILE` | Select the default template, using a path or `@name`. |
| `TEXTLLM_NAMED_TEMPLATE_<name>` | Register one name with a template file path. |
| `TEXTLLM_TEMPLATE_DIR` | Discover named templates from one directory. |

All settings are read after the existing environment-loading sequence completes. There is no implicit templates directory in this initial design; directory discovery is enabled by setting `TEXTLLM_TEMPLATE_DIR`.

### Individual Registrations

For example, in `.env`:

```dotenv
TEXTLLM_NAMED_TEMPLATE_openai="/path/to/openai.md"
TEXTLLM_NAMED_TEMPLATE_special="/another/path/custom template.md"
```

The suffix is the exact, case-sensitive name: `TEXTLLM_NAMED_TEMPLATE_openai` registers `@openai`, not `@OPENAI`.

For shell-compatible registrations, use names matching `[A-Za-z_][A-Za-z0-9_]*`. In `.bashrc`, export the variables so textllm can see them:

```bash
export TEXTLLM_NAMED_TEMPLATE_openai="$HOME/prompts/openai.md"
```

Registration values are file paths, not other named selectors. There is no alias chaining or recursive name resolution. Multiple names may point to the same file.

### Directory Discovery

```dotenv
TEXTLLM_TEMPLATE_DIR="${HOME}/.config/textllm/templates"
```

Given this directory:

```text
templates/
    openai.md
    special.md
    code-review.md
    writing help.md
```

The available names are `@openai`, `@special`, `@code-review`, and `@writing help`. Quote selectors containing spaces:

```bash
textllm --template '@writing help'
```

Discover immediate files ending in lowercase `.md`, using the filename minus that final extension as the name. Do not search subdirectories. Symlinks to files are allowed. Ignore directories and dotfiles.

Directory names may contain characters that are inconvenient in environment variable names. Individual registrations remain available for shell-compatible names. All lookups are case-sensitive, including on platforms with case-insensitive filesystems.

Named selectors must have a nonempty name and must not contain `/` or `\`, or be `.` or `..`. Directory lookup is a flat collection, not another way to specify paths. Arbitrary file paths remain supported through ordinary path selectors.

### Using Both

```dotenv
TEXTLLM_TEMPLATE_DIR="${HOME}/.config/textllm/templates"
TEXTLLM_NAMED_TEMPLATE_openai="${HOME}/work/prompts/openai.md"
TEXTLLM_NAMED_TEMPLATE_special="${TEXTLLM_TEMPLATE_DIR}/special/custom.md"
TEXTLLM_TEMPLATE_FILE=@openai
```

Here, `@openai` uses the explicitly registered work template even if the directory contains `openai.md`. `@special` can point into a subdirectory even though directory discovery itself is not recursive.

## Selection and Conflict Rules

First select the template source:

1. An explicit `--template` argument.
2. `TEXTLLM_TEMPLATE_FILE`, if configured.
3. The existing built-in template.

If the selected source is `@name`, resolve it in this order:

1. `TEXTLLM_NAMED_TEMPLATE_<name>`.
2. An exactly matching `<name>.md` discovered in `TEXTLLM_TEMPLATE_DIR`.
3. An unknown-template error.

An explicit registration wins over a directory entry regardless of which environment file supplied either setting. Environment loading determines the final values of settings; named lookup then applies the precedence above.

A registration with an empty value, a missing target, or an unreadable target is an error when selected. Do not fall back to the directory entry: that would hide a broken explicit override. An empty `TEXTLLM_TEMPLATE_DIR` disables directory discovery.

Resolve and read a selected template before creating a new conversation file, so lookup failures do not leave an empty conversation behind. Report the requested name, the relevant setting or directory, and the failing path where applicable.

An invalid configured directory causes an actionable error when directory discovery is needed or when listing templates. It does not block a directly selected file, a valid individual registration, or the built-in template.

## Paths and Environment Expansion

Relative paths are relative to the invocation's working directory, including paths supplied by environment files. They are not relative to the environment file or to `TEXTLLM_TEMPLATE_DIR`. Only directory-discovered filenames are joined to that directory.

Use `${HOME}` for portable home-directory references in `.env` files. Template lookup does not add shell evaluation or a second environment-variable expansion pass in textllm.

Python-dotenv already expands `${VAR}` references; bare `$VAR` is not expanded. Define referenced variables before their use:

```dotenv
TEXTLLM_TEMPLATE_DIR="${HOME}/prompts"
TEXTLLM_NAMED_TEMPLATE_special="${TEXTLLM_TEMPLATE_DIR}/custom.md"
```

Expansion happens while each environment file is loaded. A later file changing `TEXTLLM_TEMPLATE_DIR` does not retroactively change an already-expanded registration. Undefined references can expand to an empty string, so referenced variables should be defined explicitly. See the [python-dotenv documentation](https://github.com/theskumar/python-dotenv#variable-expansion).

Preserve textllm's existing loading order, with later files overriding earlier values:

1. Start with the process environment.
2. Load the file selected by `TEXTLLM_ENV_PATH`.
3. Load the discovered `.env` file.
4. Load the explicit `--env` file.

## Listing Templates

`--list-templates` loads environment configuration, lists effective registered and directory-discovered names in sorted order, and exits without creating a conversation or calling a model. It does not need provider credentials.

For each name, show the selector, effective path, and source. When an explicit registration overrides a directory entry, show the shadowed directory path as additional information. Quote or escape names and paths where needed so whitespace and other filename characters remain unambiguous.

Listing identifies broken entries, such as empty registrations, missing files, or invalid UTF-8, without hiding the remaining entries. Broken entries and failures to inspect the configured directory produce a nonzero exit status. An empty collection explains how to configure templates.

No template descriptions or metadata format are introduced initially.

## Conversation Behavior

Named and directory-discovered templates use the same rendering and conversation initialization behavior as existing template paths. They can contain settings, system instructions, starting messages, and the supported placeholders documented in [Template Defaults and Environment Variables](../template_defaults.md).

Templates initialize new conversations. They do not modify existing conversations or supply additional runtime defaults to them. Editing a template does not change conversations already created from it. Preserve existing behavior in which template files are only resolved and read when creating a new conversation.

Names such as `openai` are user-defined labels with no special provider or model meaning. The template contents determine those settings.

## Compatibility and Scope

Existing ordinary template paths and `TEXTLLM_TEMPLATE_FILE` defaults continue to work. The deliberate compatibility change is reserving leading `@` in selectors; existing paths beginning with that character need an explicit path prefix such as `./`.

The initial design has one configured directory, no recursive discovery, no template inheritance, no registry management commands, and no automatic modification of shell or environment files. Users manage Markdown files and environment assignments with their existing tools.

The user-facing configuration and precedence rules are documented in [Template Defaults and Environment Variables](../template_defaults.md), with a short example in the [README](../../readme.md). This document records the design rationale.
