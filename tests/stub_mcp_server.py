import asyncio
import sys
from io import TextIOWrapper
from typing import Any, cast

import anyio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

app = Server("stub-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="echo",
            description="Echo the provided message",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                    }
                },
                "required": ["message"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict | None) -> CallToolResult:
    if name != "echo":
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )

    message = (arguments or {}).get("message", "")
    return CallToolResult(
        content=[TextContent(type="text", text=str(message))],
        isError=False,
    )


async def main() -> None:
    stdin_stream = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))

    class _FilteredAsyncFile:
        def __init__(self, wrapped):
            self._wrapped = wrapped
            self._iterator = wrapped.__aiter__()

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def __aiter__(self):
            return self

        async def __anext__(self):
            while True:
                try:
                    line = await self._iterator.__anext__()
                except StopAsyncIteration:
                    raise
                if not line.strip():
                    continue
                return line

        async def aclose(self):
            await self._wrapped.aclose()

        async def close(self):
            await self._wrapped.close()

    filtered_stdin = _FilteredAsyncFile(stdin_stream)

    async with stdio_server(stdin=cast(Any, filtered_stdin)) as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
