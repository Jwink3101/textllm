# Changelog

## 0.1.1 (2025-02-03)

(Bumping second digit. Should have done 0.1.0 instead of 0.0.5)

Added --env, documented options for environment files, and bug fixes.

## 0.0.5 (2025-02-03)

Made it so if you specify `--edit --edit`, it will create a recursive loop calling the editor after each chat too. This makes it *almost* like an interactive chat. To exit, just exit the editor without saving. Note that `--prompt` is only applied on the first round.

Also some documentation tweaks.

## 0.0.4 (2025-02-02)

- Added `--stream / --no-stream` to stream the model output to stdout in addition to the file.
- Added `--prompt` and `--edit` flags edit or add a new prompt.

The two above flags make it possible to treat textllm as a simple client without even opening the file!

Minor:

- Fixed bug that made textllm still rename a file.
- Decided to *remove* all of the commented models. They are added to the readme.

## 0.0.3 (2025-02-02)

More commented models in the template. Move to different numbering scheme

## 20250201.0.xx

These are the initial release but also in flux (the different .xx) as I work out kinks. Still. **BETA**
