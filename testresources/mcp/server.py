"""Deterministic local MCP fixture; never contacts external services."""

import os

from mcp.server import MCPServer

server = MCPServer("textllm-test")


@server.tool()
def echo(value: str) -> dict:
    """Echo a value as structured JSON."""
    return {"value": value}


@server.tool()
def failure() -> str:
    """Return a deterministic execution error."""
    raise ValueError("fixture failure")


@server.tool()
def environment() -> str:
    """Read the explicit fixture environment value."""
    return os.environ.get("TEXTLLM_MCP_FIXTURE", "missing")


if __name__ == "__main__":
    server.run(transport="stdio")
