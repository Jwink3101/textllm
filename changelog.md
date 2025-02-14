# Changelog

## 0.6.0 (2025-02-14)

- Fixed bug with reading stdin and launching editor
- Added `--stdin` instead of `--prompt -` so that you can do both
- Changed `--silent` to `--quiet`
- Added "Created with {version} at {now}" to the template and those are now filliable

## 0.5.1 (2025-02-13)

- The environment is now loaded dynamically. This means that you can specify an environment file with textllm settings as well (except for the `$TEXTLLM_ENV_PATH` obviously)
- Adds `loads()` to help parse the file into a dictionary

## 0.5.0 (2025-02-05)

Introduces (and tests) the ability to parse Markdown images

## 0.4.1 (2025-02-05)

- Introduced `$TEXTLLM_DEFAULT_MODEL` and `$TEXTLLM_DEFAULT_TEMPERATURE` to control default behavior for *both* new template files and if it's not set
- Introduced `$TEXTLLM_TEMPLATE_FILE` to specify a new template file. Does *not* include `$TEXTLLM_DEFAULT_MODEL` and `$TEXTLLM_DEFAULT_TEMPERATURE`

## 0.4.0 (2025-02-05)

- Removed environment settings `$TEXTLLM_AUTO_RENAME` and `$TEXTLLM_STREAM` because as I use it, I don't see the utility. `--rename` default is based on whether a file is specified. There is little reason to universally set --no-stream.

Shorted the default system prompt and cleanup help message

## 0.3.0 (2025-02-05)

- Made specifying a filename optional. Instead, will create a new file. If an *existing* directory is specified, will create the new file there.
    - `--rename` defaults to True if a new file is created. Still defaults to `$TEXTLLM_AUTO_RENAME` otherwise which itself defaults to False
- Removed `--create` setting. Specifying a new file is all that is needed.
- Removed the double `--edit` where it opened up again. I think it's not worth it. Better to just have the user submit again.

## 0.2.0 (2025-02-04)

- Changed default to `--stream`
- Added a tester. It's not perfect but it covers most cases

Minor:

- More robust parsing of settings

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
