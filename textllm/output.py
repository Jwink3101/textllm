"""Coordinate streamed stdout with console logging."""

import logging
from contextvars import ContextVar


_active_output = ContextVar("textllm_stream_output", default=None)


class ConsoleHandler(logging.StreamHandler):
    """Start console records on a fresh line during streamed output."""

    def emit(self, record):
        output = _active_output.get()
        if output is not None and output.mid_line:
            output.write("\n")
        super().emit(record)


class StreamOutput:
    """Track the current stdout line for the duration of a model stream."""

    def __init__(self, enabled):
        self.enabled = enabled
        self.mid_line = False

    def __enter__(self):
        self.token = _active_output.set(self if self.enabled else None)
        return self

    def __exit__(self, *exc):
        _active_output.reset(self.token)

    def write(self, text):
        """Print a chunk and remember whether it ends within a line."""
        if self.enabled and text:
            print(text, end="", flush=True)
            self.mid_line = not text.endswith("\n")
