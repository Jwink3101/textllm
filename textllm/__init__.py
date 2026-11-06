"""Text-file based conversations with streaming language models.

Public imports remain available here; implementation belongs to the focused
modules alongside this file.
"""

from ._version import __version__
from .backends import (
    LLMResponse,
    ModelEvent,
    iter_completion_text,
    iter_messages_events,
    iter_model_events,
    iter_responses_events,
    split_model_provider,
)
from .cli import cli
from .config import CONFIG, DEFAULT_FILEPATH, log
from .conversation import Conversation, NoHumanMessageError, NoUserMessageError
from .files import clean_filepath, file_edit, title2filename, uniqueify_filepath
from .markdown import (
    grouper,
    loads,
    merge_message_runs,
    process_msg_for_attachments,
    process_msg_for_images,
)
