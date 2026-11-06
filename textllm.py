#!/usr/bin/env python
"""Run the development checkout, regardless of an installed textllm version."""

import sys
from pathlib import Path


def _main():
    # Resolve relative to this launcher, including when called from another
    # directory or through a symlink. Never fall back to an installed version
    # if the launcher has been separated from the checkout.
    checkout = Path(__file__).resolve().parent
    if not (checkout / "textllm" / "__init__.py").is_file():
        raise SystemExit(
            "The development launcher requires the adjacent textllm package."
        )
    sys.path.insert(0, str(checkout))

    from textllm import cli

    cli()


if __name__ == "__main__":
    _main()
