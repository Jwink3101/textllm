"""Local tool configuration and MCP execution, independent of model providers."""

import copy
import hashlib
import json
import math
import os
import re
import shlex
from contextlib import AsyncExitStack, asynccontextmanager
from time import monotonic
from urllib.parse import urlsplit

from .config import _resolve_settings, log


@asynccontextmanager
async def _log_operation(server, operation):
    """Log MCP setup operations without exposing connection or protocol data."""
    log.debug("MCP %s started: %r", operation, server)
    started = monotonic()
    try:
        yield
    except BaseException:
        log.debug("MCP %s failed: %r (%.2fs)", operation, server, monotonic() - started)
        raise
    else:
        log.debug("MCP %s completed: %r (%.2fs)", operation, server, monotonic() - started)


LIMIT_DEFAULTS = {
    "max_rounds": 10,
    "max_calls": 50,
    "timeout": 60,
    "max_result_chars": 100_000,
    "max_context_chars": 1_000_000,
}


def tool_limits(settings):
    """Validate local execution limits and return their effective values.

    Parameters
    ----------
    settings : dict
        Local tool settings.

    Returns
    -------
    dict
        Positive, finite limits.
    """
    if not isinstance(settings, dict) or settings.keys() - LIMIT_DEFAULTS.keys():
        raise ValueError("textllm.tools contains unknown limits")
    limits = LIMIT_DEFAULTS | settings
    for name, value in limits.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or (name != "timeout" and not isinstance(value, int))
        ):
            raise ValueError(
                f"textllm.tools.{name} must be a positive finite "
                + ("number" if name == "timeout" else "integer")
            )
    return limits


def mcp_config(entries, *, expand=True):
    """Validate and expand inline MCP servers without launching processes.

    Parameters
    ----------
    entries : list of dict
        Servers from ``textllm.mcp``.
    expand : bool, optional
        Expand environment references; disable for already resolved settings.

    Returns
    -------
    list of dict
        Resolved connection settings. These can contain secrets; never log them.
    """
    if expand:
        entries = _resolve_settings({"textllm": {"mcp": entries}})["textllm"]["mcp"]
    if not isinstance(entries, list):
        raise ValueError("textllm.mcp must be an array of tables")
    result, names = [], set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.keys() - {
            "url",
            "command",
            "name",
            "env",
            "bearer_token",
        }:
            raise ValueError("Invalid MCP server settings")
        if ("url" in entry) == ("command" in entry):
            raise ValueError("Each MCP server requires exactly one of url or command")
        name = entry.get("name", f"server_{index + 1}")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
            raise ValueError("MCP name must contain 1-32 letters, digits, '_' or '-'")
        if name in names:
            raise ValueError(f"Duplicate MCP server name: {name}")
        names.add(name)
        resolved = {"name": name}
        if "url" in entry:
            url = entry["url"]
            if not isinstance(url, str):
                raise ValueError("MCP URL must be a string")
            parts = urlsplit(url)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError(
                    "MCP URL must be HTTP(S), without embedded credentials"
                )
            if "env" in entry:
                raise ValueError("MCP env is only supported for stdio")
            resolved["url"] = url
            if "bearer_token" in entry:
                token = entry["bearer_token"]
                if not isinstance(token, str) or not token:
                    raise ValueError("MCP bearer_token must be a non-empty string")
                resolved["token"] = token
        else:
            if "bearer_token" in entry:
                raise ValueError("MCP bearer_token is only supported for HTTP")
            command = entry["command"]
            if isinstance(command, str):
                command = shlex.split(command)
            if not isinstance(command, list) or not command:
                raise ValueError(
                    "MCP command must be a non-empty string or argument array"
                )
            if any(not isinstance(arg, str) for arg in command):
                raise ValueError("MCP command arguments must be strings")
            resolved["command"] = command.copy()
            if not resolved["command"][0]:
                raise ValueError("MCP executable must not be empty")
            env = entry.get("env", {})
            if not isinstance(env, dict) or any(
                not isinstance(k, str) or not k or "=" in k for k in env
            ):
                raise ValueError("MCP env must be a table of environment variables")
            if any(not isinstance(value, str) for value in env.values()):
                raise ValueError("MCP environment values must be strings")
            resolved["env"] = env.copy()
        result.append(resolved)
    return result


class MCPExecutor:
    """Connect configured MCP servers and expose a synchronous tool executor.

    Parameters
    ----------
    servers : list of dict
        Validated, expanded server configuration.
    timeout : float, optional
        Per-operation timeout in seconds.
    """

    def __init__(self, servers, timeout=60):
        self.servers = servers
        self.timeout = timeout
        self.tools = []
        self.routes = {}

    @asynccontextmanager
    async def _connections(self):
        import httpx2
        from mcp import Client, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        async with AsyncExitStack() as stack:
            # Server stderr can contain credentials or protocol diagnostics. It
            # is not model output and is deliberately not copied into history.
            errlog = stack.enter_context(open(os.devnull, "w"))
            for server in self.servers:
                if "command" in server:
                    command, *args = server["command"]
                    params = StdioServerParameters(
                        command=command,
                        args=args,
                        env=os.environ.copy() | server["env"],
                    )
                    transport = stdio_client(params, errlog=errlog)
                else:
                    headers = (
                        {"Authorization": f"Bearer {server['token']}"}
                        if "token" in server
                        else {}
                    )
                    client = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            headers=headers,
                            timeout=self.timeout,
                            follow_redirects=False,
                        )
                    )
                    transport = streamable_http_client(
                        server["url"], http_client=client
                    )
                # Entering the SDK client opens the transport and initializes
                # the session, including SDK-managed protocol negotiation.
                async with _log_operation(
                    server["name"], "connection/session initialization"
                ):
                    session = await stack.enter_async_context(
                        Client(transport, read_timeout_seconds=self.timeout, cache=None)
                    )
                cursor, seen = None, set()
                page, tool_count = 0, 0
                while True:
                    page += 1
                    async with _log_operation(
                        server["name"], f"tools/list page {page}"
                    ):
                        listing = await session.list_tools(cursor=cursor)
                    tool_count += len(listing.tools)
                    log.debug(
                        "MCP tools/list page %d: %r (%d tools, more pages: %s)",
                        page,
                        server["name"],
                        len(listing.tools),
                        bool(listing.next_cursor),
                    )
                    for tool in listing.tools:
                        # Stable provider-safe names, including long/unusual MCP names.
                        digest = hashlib.sha256(tool.name.encode()).hexdigest()[:12]
                        safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool.name)[:16]
                        name = f"{server['name']}__{safe}_{digest}"
                        if name in self.routes:
                            raise ValueError("MCP server returned duplicate tool names")
                        schema = tool.input_schema
                        from jsonschema.validators import validator_for

                        validator_for(schema).check_schema(schema)
                        self.tools.append(
                            {
                                "name": name,
                                "description": tool.description or "",
                                "parameters": schema,
                            }
                        )
                        self.routes[name] = (session, server["name"], tool.name, schema)
                    cursor = listing.next_cursor
                    if not cursor:
                        break
                    if cursor in seen:
                        raise ValueError("MCP tools/list repeated a pagination cursor")
                    seen.add(cursor)
                    if len(seen) >= 100:
                        raise ValueError("MCP tools/list pagination limit exceeded")
                log.debug(
                    "MCP discovery completed: %r (%d tools, %d pages)",
                    server["name"],
                    tool_count,
                    page,
                )
            self.tools.sort(key=lambda tool: tool["name"])
            yield self

    def __enter__(self):
        from anyio.from_thread import start_blocking_portal

        self._portal_context = start_blocking_portal()
        self.portal = self._portal_context.__enter__()
        self._connection_context = self.portal.wrap_async_context_manager(
            self._connections()
        )
        try:
            return self._connection_context.__enter__()
        except BaseException as error:
            self._portal_context.__exit__(None, None, None)
            if not isinstance(error, Exception):
                raise
            raise RuntimeError(
                "MCP connection or discovery failed; check server configuration"
            ) from None

    def __exit__(self, *exc):
        try:
            return self._connection_context.__exit__(None, None, None)
        finally:
            self._portal_context.__exit__(None, None, None)

    def validate(self, call):
        """Validate a complete model call before execution.

        Parameters
        ----------
        call : dict
            Normalized name, ID, and JSON argument string.

        Returns
        -------
        dict
            Parsed arguments.
        """
        from jsonschema.validators import validator_for
        from referencing import Registry

        if call["name"] not in self.routes:
            raise ValueError("Model requested an unavailable MCP tool")

        def invalid_constant(value):
            raise ValueError("Tool arguments must be valid JSON")

        arguments = json.loads(call["arguments"], parse_constant=invalid_constant)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        schema = self.routes[call["name"]][3]
        validator_for(schema)(schema, registry=Registry()).validate(arguments)
        return arguments

    def identity(self, call):
        """Return non-secret server and tool identity for a validated call.

        Parameters
        ----------
        call : dict
            Normalized tool call.

        Returns
        -------
        dict
            Persistent executor identity.
        """
        _, server, tool, _ = self.routes[call["name"]]
        return {"executor": "mcp", "server": server, "tool": tool}

    def execute(self, call, arguments):
        """Execute an MCP call once and return its JSON-compatible result.

        Parameters
        ----------
        call : dict
            Normalized tool call.
        arguments : dict
            Previously validated arguments.

        Returns
        -------
        dict
            Native MCP result, including ``isError`` when supplied.
        """
        import anyio

        session, _, name, _ = self.routes[call["name"]]

        async def invoke():
            with anyio.fail_after(self.timeout):
                return await session.call_tool(name, arguments)

        try:
            return self.portal.call(invoke).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        except Exception:
            # A transport failure does not prove that an external action failed.
            raise RuntimeError(
                "MCP call interrupted or transport failed; outcome unknown, do not automatically retry"
            ) from None


def model_result(result, max_chars):
    """Select supported result content without duplicating structured JSON.

    Parameters
    ----------
    result : dict
        Native MCP result.
    max_chars : int
        Maximum model-facing result size.

    Returns
    -------
    tuple of (str, bool)
        Model-facing output and error flag.
    """
    if result.get("resultType", "complete") != "complete":
        return "Unsupported MCP input-required interaction", True
    content = result.get("content", [])
    unsupported = sorted(
        {item.get("type", "unknown") for item in content if item.get("type") != "text"}
    )
    if unsupported:
        return "Unsupported MCP output types: " + ", ".join(unsupported), True
    if "structuredContent" in result:
        output = json.dumps(result["structuredContent"], ensure_ascii=True)
    else:
        output = "\n".join(item.get("text", "") for item in content)
    if len(output) > max_chars:
        return (
            f"MCP result exceeded max_result_chars ({max_chars}); original retained in sidecar",
            True,
        )
    return output, bool(result.get("isError"))


def tool_definitions(tools, backend):
    """Adapt generic tool definitions for a model API.

    Parameters
    ----------
    tools : list of dict
        Generic tool definitions.
    backend : str
        Model API backend.

    Returns
    -------
    list of dict
        Native provider tool declarations.
    """
    result = []
    for source in tools:
        tool = copy.deepcopy(source)
        if backend == "completion":
            result.append({"type": "function", "function": tool})
        elif backend == "responses":
            result.append({"type": "function", **tool})
        else:
            tool["input_schema"] = tool.pop("parameters")
            result.append(tool)
    return result
