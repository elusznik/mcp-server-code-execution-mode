#!/usr/bin/env python3
"""MCP Server Code Execution Mode bridge backed by a containerised sandbox."""

from __future__ import annotations

import asyncio
import copy
import json
import keyword
import logging
import os
import re
import shutil
import sys
import tempfile
import textwrap
from asyncio import subprocess as aio_subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Sequence, cast

try:  # Prefer the official encoder when available
    import toon_format as _toon_format
    _toon_encode = _toon_format.encode
except ImportError:  # pragma: no cover - fallback for environments without toon
    _toon_encode = None

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import (
    INVALID_PARAMS,
    CallToolResult,
    ErrorData,
    Resource,
    TextContent,
    Tool,
)

logger = logging.getLogger("mcp-server-code-execution-mode")

BRIDGE_NAME = "mcp-server-code-execution-mode"
DEFAULT_IMAGE = os.environ.get("MCP_BRIDGE_IMAGE", "python:3.14-slim")
DEFAULT_RUNTIME = os.environ.get("MCP_BRIDGE_RUNTIME")
DEFAULT_TIMEOUT = int(os.environ.get("MCP_BRIDGE_TIMEOUT", "30"))
MAX_TIMEOUT = int(os.environ.get("MCP_BRIDGE_MAX_TIMEOUT", "120"))
DEFAULT_MEMORY = os.environ.get("MCP_BRIDGE_MEMORY", "512m")
DEFAULT_PIDS = int(os.environ.get("MCP_BRIDGE_PIDS", "128"))
DEFAULT_CPUS = os.environ.get("MCP_BRIDGE_CPUS")
CONTAINER_USER = os.environ.get("MCP_BRIDGE_CONTAINER_USER", "65534:65534")
DEFAULT_RUNTIME_IDLE_TIMEOUT = int(os.environ.get("MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT", "300"))

_PODMAN_PULL_PREFIXES: tuple[str, ...] = (
    'Resolved "',
    "Trying to pull",
    "Getting image source signatures",
    "Copying blob",
    "Copying config",
    "Extracting",
    "Writing manifest",
    "Storing signatures",
)

SANDBOX_HELPERS_SUMMARY = (
    "Helpers (after `import mcp.runtime as runtime`): await runtime.list_servers() or call runtime.list_servers_sync(), "
    "runtime.discovered_servers(), runtime.list_tools_sync(server), runtime.query_tool_docs[_sync], "
    "runtime.search_tool_docs[_sync], runtime.describe_server(name) (includes 'cwd' if configured), runtime.list_loaded_server_metadata(), runtime.capability_summary() "
    "(prints this digest). Loaded servers also expose mcp_<alias> proxies."
)

_NOISE_STREAM_TOKENS = {"()"}

CAPABILITY_RESOURCE_URI = "resource://mcp-server-code-execution-mode/capabilities"
_CAPABILITY_RESOURCE_NAME = "code-execution-capabilities"
_CAPABILITY_RESOURCE_TITLE = "Code Execution Sandbox Helpers"
_CAPABILITY_RESOURCE_DESCRIPTION = (
    "Capability overview, helper reference, and sandbox usage notes (call runtime.capability_summary() inside the sandbox for this text)."
)
_CAPABILITY_RESOURCE_TEXT = textwrap.dedent(
    f"""
    # Code Execution MCP Capabilities

    {SANDBOX_HELPERS_SUMMARY}

    ## Quick usage

    - Pass `servers=[...]` to mount MCP proxies (`mcp_<alias>` modules).
    - Import `mcp.runtime as runtime`; call `runtime.capability_summary()` instead of rereading this resource for the same hint.
    - Prefer the `_sync` helpers first to read cached metadata before issuing RPCs.
        - Server configs support a `cwd` field to start the host MCP server in a specific working directory.
        - LLMs should check `runtime.describe_server(name)` or `runtime.list_loaded_server_metadata()` for the server's configured `cwd` before assuming the working directory.
            If `cwd` is absent, the host starts the server in the bridge process' current directory (i.e., the default working directory). If your workload expects a specific working directory, please configure `cwd` in the server config or run the server in a container that mounts the project directory.

    Resource URI: {CAPABILITY_RESOURCE_URI}
    """
).strip()


def _build_capability_resource() -> Resource:
    return Resource(
        name=_CAPABILITY_RESOURCE_NAME,
        title=_CAPABILITY_RESOURCE_TITLE,
        description=_CAPABILITY_RESOURCE_DESCRIPTION,
        uri=CAPABILITY_RESOURCE_URI,  # type: ignore[arg-type]
        mimeType="text/markdown",
        size=len(_CAPABILITY_RESOURCE_TEXT.encode("utf-8")),
    )

CONFIG_DIRS = [
    Path.home() / ".config" / "mcp" / "servers",
    Path.home() / "Library" / "Application Support" / "Claude Code" / "mcp" / "servers",
    Path.home() / "Library" / "Application Support" / "Claude" / "mcp" / "servers",
    Path.cwd() / "mcp-servers",
]
CLAUDE_CONFIG_PATHS = [
    Path.home() / ".claude.json",
    Path.home() / "Library" / "Application Support" / "Claude Code" / "claude_code_config.json",
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_code_config.json",
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    Path.cwd() / "claude_code_config.json",
    Path.cwd() / "claude_desktop_config.json",
]


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot execute user code."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class ClientLike(Protocol):
    async def list_tools(self) -> List[Dict[str, object]]:  # pragma: no cover - typing only
        ...

    async def call_tool(self, name: str, arguments: Dict[str, object]) -> Dict[str, object]:  # pragma: no cover - typing only
        ...

    async def stop(self) -> None:  # pragma: no cover - typing only
        ...


class SandboxLike(Protocol):
    async def execute(self, code: str, **kwargs) -> SandboxResult:  # pragma: no cover - typing only
        ...

    async def ensure_shared_directory(self, path: Path) -> None:  # pragma: no cover - typing only
        ...


class SandboxTimeout(SandboxError):
    """Raised when user code exceeds the configured timeout."""


@dataclass
class SandboxResult:
    """Execution result captured from the sandbox."""

    success: bool
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class MCPServerInfo:
    """Configuration for a single MCP server binary."""

    name: str
    command: str
    args: List[str]
    env: Dict[str, str]
    cwd: Optional[str] = None


def _split_output_lines(stream: Optional[str]) -> List[str]:
    """Return a newline-preserving list for stdout/stderr fields."""

    if not stream:
        return []
    return stream.splitlines()


def _filter_stream_lines(lines: Sequence[str]) -> List[str]:
    """Drop whitespace-only or noise-only lines to save response tokens."""

    filtered: List[str] = []
    for line in lines:
        text = str(line)
        stripped = text.strip()
        if not stripped or stripped in _NOISE_STREAM_TOKENS:
            continue
        filtered.append(text)
    return filtered


def _render_toon_block(payload: Dict[str, object]) -> str:
    """Encode a payload in TOON format, falling back to JSON when unavailable."""

    if _toon_encode is not None:
        try:
            body = _toon_encode(payload)
        except Exception:  # pragma: no cover - defensive fallback
            logger.debug("Failed to encode payload as TOON", exc_info=True)
        else:
            body = body.rstrip()
            return f"```toon\n{body}\n```" if body else "```toon\n```"

    fallback = json.dumps(payload, indent=2, sort_keys=True)
    return f"```json\n{fallback}\n```"


def _output_mode() -> str:
    """Return the configured output mode."""

    return os.environ.get("MCP_BRIDGE_OUTPUT_MODE", "compact").strip().lower()


def _render_compact_output(payload: Dict[str, object]) -> str:
    """Render a terse, token-efficient textual summary."""

    lines: List[str] = []
    stdout_raw = payload.get("stdout", ())
    if isinstance(stdout_raw, (list, tuple)):
        stdout_lines = list(stdout_raw)
    else:
        stdout_lines = []
    stderr_raw = payload.get("stderr", ())
    if isinstance(stderr_raw, (list, tuple)):
        stderr_lines = list(stderr_raw)
    else:
        stderr_lines = []
    if stdout_lines:
        lines.append("\n".join(str(item) for item in stdout_lines))
    if stderr_lines:
        stderr_text = "\n".join(str(item) for item in stderr_lines)
        lines.append(f"stderr:\n{stderr_text}")

    status = str(payload.get("status", ""))
    exit_code = payload.get("exitCode")
    error = payload.get("error")

    if not lines and payload.get("summary"):
        lines.append(str(payload["summary"]))

    if error and (not lines or status != "error"):
        lines.append(f"error: {error}")

    if exit_code not in (None, 0):
        lines.insert(0, f"exit: {exit_code}")

    if status and status.lower() not in {"", "success"}:
        lines.insert(0, f"status: {status}")

    text = "\n".join(line for line in lines if line).strip()
    if text:
        return text

    if status:
        return status
    return str(payload.get("summary", "")).strip() or "success"


def _build_compact_structured_payload(payload: Dict[str, object]) -> Dict[str, object]:
    """Return a trimmed structured representation for compact responses."""

    compact: Dict[str, object] = {}
    status = str(payload.get("status", ""))
    exit_code = payload.get("exitCode")

    if status and status.lower() != "success":
        compact["status"] = status

    if exit_code not in (None, 0):
        compact["exitCode"] = exit_code

    if payload.get("stdout"):
        compact["stdout"] = payload["stdout"]

    if payload.get("stderr"):
        compact["stderr"] = payload["stderr"]

    if payload.get("servers"):
        compact["servers"] = payload["servers"]

    if payload.get("timeoutSeconds"):
        compact["timeoutSeconds"] = payload["timeoutSeconds"]

    if payload.get("error"):
        compact["error"] = payload["error"]

    summary = payload.get("summary")
    if summary and (status.lower() != "success" or not compact.get("stdout")):
        compact["summary"] = summary

    return compact or {key: payload[key] for key in ("status", "summary") if key in payload}


def _build_response_payload(
    *,
    status: str,
    summary: str,
    exit_code: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    servers: Optional[Sequence[str]] = None,
    error: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, object]:
    """Create a structured payload shared by compact/TOON responses."""

    summary_lower = summary.strip().lower()
    payload: Dict[str, object] = {
        "status": status,
        "summary": summary,
    }

    if exit_code is not None:
        payload["exitCode"] = exit_code
    if servers:
        payload["servers"] = list(servers)

    stdout_lines = _filter_stream_lines(_split_output_lines(stdout))
    if stdout_lines:
        payload["stdout"] = stdout_lines

    stderr_lines = _filter_stream_lines(_split_output_lines(stderr))
    if stderr_lines:
        payload["stderr"] = stderr_lines

    if error:
        payload["error"] = error
    if timeout_seconds is not None:
        payload["timeoutSeconds"] = timeout_seconds

    if (
        status.lower() == "success"
        and not payload.get("stdout")
        and not payload.get("stderr")
        and summary_lower == "success"
    ):
        payload["summary"] = "Success (no output)"

    return {
        key: value
        for key, value in payload.items()
        if not _is_empty_field(value)
    }


def _is_empty_field(value: object) -> bool:
    """Return True when a structured field should be omitted."""

    if value is None:
        return True
    if isinstance(value, (list, tuple, set, dict, str)):
        return len(value) == 0
    return False


def _build_tool_response(
    *,
    status: str,
    summary: str,
    exit_code: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    servers: Optional[Sequence[str]] = None,
    error: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
) -> CallToolResult:
    """Render a tool response in compact text (default) or TOON format."""

    payload = _build_response_payload(
        status=status,
        summary=summary,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        servers=servers,
        error=error,
        timeout_seconds=timeout_seconds,
    )
    status = str(payload.get("status", "error")).lower()
    is_error = status not in {"success"}
    mode = _output_mode()

    if mode == "compact":
        message = _render_compact_output(payload)
        structured = _build_compact_structured_payload(payload)
        return CallToolResult(
            content=[TextContent(type="text", text=message)],
            structuredContent=structured,
            isError=is_error,
        )

    message = _render_toon_block(payload)
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=payload,
        isError=is_error,
    )


def _sanitize_identifier(value: str, *, default: str) -> str:
    """Convert an arbitrary string into a valid Python identifier."""

    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip())
    cleaned = cleaned.lower() or default
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


class PersistentMCPClient:
    """Maintain a persistent MCP stdio session."""

    def __init__(self, server_info: MCPServerInfo) -> None:
        self.server_info = server_info
        self._stdio_cm = None
        self._session: Optional[ClientSession] = None

    async def start(self) -> None:
        if self._session:
            return

        params = StdioServerParameters(
            command=self.server_info.command,
            args=self.server_info.args,
            env=self.server_info.env or None,
            cwd=self.server_info.cwd or None,
        )

        client_cm = stdio_client(params)
        self._stdio_cm = client_cm
        read_stream, write_stream = await client_cm.__aenter__()

        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()
        self._session = session

    async def list_tools(self) -> List[Dict[str, object]]:
        if not self._session:
            raise SandboxError("MCP client not started")

        result = await self._session.list_tools()
        return [tool.model_dump(by_alias=True, exclude_none=True) for tool in result.tools]

    async def call_tool(self, name: str, arguments: Dict[str, object]) -> Dict[str, object]:
        if not self._session:
            raise SandboxError("MCP client not started")

        call_result = await self._session.call_tool(name=name, arguments=arguments)
        return call_result.model_dump(by_alias=True, exclude_none=True)

    async def stop(self) -> None:
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except* Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug("MCP session shutdown raised %s", exc, exc_info=True)
            finally:
                self._session = None
        if self._stdio_cm:
            try:
                await self._stdio_cm.__aexit__(None, None, None)  # type: ignore[union-attr]
            except* Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug("MCP stdio shutdown raised %s", exc, exc_info=True)
            finally:
                self._stdio_cm = None


class RootlessContainerSandbox:
    """Execute Python code in a locked-down container."""

    def __init__(
        self,
        *,
        runtime: Optional[str] = None,
        image: str = DEFAULT_IMAGE,
        memory_limit: str = DEFAULT_MEMORY,
        pids_limit: int = DEFAULT_PIDS,
        cpu_limit: Optional[str] = DEFAULT_CPUS,
        runtime_idle_timeout: int = DEFAULT_RUNTIME_IDLE_TIMEOUT,
    ) -> None:
        self.runtime = detect_runtime(runtime)
        self.image = image
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit
        self.cpu_limit = cpu_limit
        self._runtime_check_lock = asyncio.Lock()
        self.runtime_idle_timeout = max(0, runtime_idle_timeout)
        self._shutdown_task: Optional[asyncio.Task[None]] = None
        self._share_lock = asyncio.Lock()
        self._shared_paths: set[str] = set()

    def _base_cmd(self) -> List[str]:
        cmd: List[str] = [
            self.runtime,
            "run",
            "--rm",
            "--interactive",
            "--network",
            "none",
            "--read-only",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory_limit,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--tmpfs",
            "/workspace:rw,noexec,nosuid,nodev,size=128m",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/workspace",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--user",
            CONTAINER_USER,
        ]
        if self.cpu_limit:
            cmd.extend(["--cpus", self.cpu_limit])
        return cmd

    def _render_entrypoint(
        self,
        code: str,
        server_metadata: Sequence[Dict[str, object]],
        discovered_servers: Sequence[str],
    ) -> str:
        metadata_json = json.dumps(server_metadata, separators=(",", ":"))
        discovered_json = json.dumps(list(discovered_servers), separators=(",", ":"))
        template = textwrap.dedent(
            """
            import asyncio
            import inspect
            import json
            import sys
            import traceback
            import types
            from contextlib import suppress

            AVAILABLE_SERVERS = json.loads(__METADATA_JSON__)
            DISCOVERED_SERVERS = json.loads(__DISCOVERED_JSON__)
            CODE = __CODE_LITERAL__

            _PENDING_RESPONSES = {}
            _REQUEST_COUNTER = 0
            _READER_TASK = None

            def _send_message(message):
                sys.__stdout__.write(json.dumps(message, separators=(",", ":")) + "\\n")
                sys.__stdout__.flush()

            class _StreamProxy:
                def __init__(self, kind):
                    self._kind = kind

                def write(self, data):
                    if not data:
                        return
                    _send_message({"type": self._kind, "data": data})

                def flush(self):
                    pass

                def isatty(self):
                    return False

            sys.stdout = _StreamProxy("stdout")
            sys.stderr = _StreamProxy("stderr")

            async def _stdin_reader():
                loop = asyncio.get_running_loop()
                reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(reader)
                transport = None

                try:
                    transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        try:
                            message = json.loads(line.decode())
                        except Exception:
                            continue
                        if message.get("type") != "rpc_response":
                            continue
                        request_id = message.get("id")
                        future = _PENDING_RESPONSES.pop(request_id, None)
                        if future and not future.done():
                            if message.get("success", True):
                                future.set_result(message.get("payload"))
                            else:
                                future.set_exception(RuntimeError(message.get("error", "RPC error")))
                finally:
                    if transport is not None:
                        transport.close()
                    for future in list(_PENDING_RESPONSES.values()):
                        if not future.done():
                            future.set_exception(RuntimeError("RPC channel closed"))

            async def _ensure_reader():
                global _READER_TASK
                if _READER_TASK is None:
                    _READER_TASK = asyncio.create_task(_stdin_reader())

            async def _rpc_call(payload):
                await _ensure_reader()
                loop = asyncio.get_running_loop()
                global _REQUEST_COUNTER
                _REQUEST_COUNTER += 1
                request_id = _REQUEST_COUNTER
                future = loop.create_future()
                _PENDING_RESPONSES[request_id] = future
                _send_message({"type": "rpc_request", "id": request_id, "payload": payload})
                return await future

            def _install_mcp_modules():
                mcp_pkg = types.ModuleType("mcp")
                mcp_pkg.__path__ = []
                mcp_pkg.__all__ = ["runtime", "servers"]
                sys.modules["mcp"] = mcp_pkg

                runtime_module = types.ModuleType("mcp.runtime")
                servers_module = types.ModuleType("mcp.servers")
                servers_module.__path__ = []
                sys.modules["mcp.runtime"] = runtime_module
                sys.modules["mcp.servers"] = servers_module
                mcp_pkg.runtime = runtime_module
                mcp_pkg.servers = servers_module

                class MCPError(RuntimeError):
                    'Raised when an MCP call fails.'

                _CAPABILITY_SUMMARY = (
                    "locked-down Python sandbox; load MCP servers via the 'servers' argument. After `import mcp.runtime as runtime`, "
                    "use runtime.list_servers_sync()/await runtime.list_servers(), runtime.discovered_servers(), runtime.list_tools_sync(server), "
                    "runtime.query_tool_docs[_sync], runtime.search_tool_docs[_sync], runtime.describe_server(), runtime.list_loaded_server_metadata(), "
                    "runtime.capability_summary(). Loaded servers expose mcp_<alias> proxies."
                )

                _LOADED_SERVER_NAMES = tuple(server.get("name") for server in AVAILABLE_SERVERS)

                def _lookup_server(name):
                    for server in AVAILABLE_SERVERS:
                        if server.get("name") == name:
                            return server
                    raise MCPError(f"Server {name!r} is not loaded")

                def _normalise_detail(value):
                    detail = str(value).lower() if value is not None else "summary"
                    return detail if detail in {"summary", "full"} else "summary"

                def _format_tool_doc(server_info, tool_info, detail):
                    doc = {
                        "server": server_info.get("name"),
                        "serverAlias": server_info.get("alias"),
                        "tool": tool_info.get("name"),
                        "toolAlias": tool_info.get("alias"),
                    }
                    description = tool_info.get("description")
                    if description:
                        doc["description"] = description
                    if detail == "full" and tool_info.get("input_schema") is not None:
                        doc["inputSchema"] = tool_info.get("input_schema")
                    return doc

                async def call_tool(server, tool, arguments=None):
                    response = await _rpc_call(
                        {
                            "type": "call_tool",
                            "server": server,
                            "tool": tool,
                            "arguments": arguments or {},
                        }
                    )
                    if not response.get("success", True):
                        raise MCPError(response.get("error", "MCP request failed"))
                    return response.get("result")

                async def list_tools(server):
                    response = await _rpc_call(
                        {
                            "type": "list_tools",
                            "server": server,
                        }
                    )
                    if not response.get("success", True):
                        raise MCPError(response.get("error", "MCP request failed"))
                    return response.get("tools", [])

                async def list_servers():
                    response = await _rpc_call({"type": "list_servers"})
                    if not response.get("success", True):
                        raise MCPError(response.get("error", "MCP request failed"))
                    return tuple(response.get("servers", ()))

                def list_servers_sync():
                    return tuple(name for name in _LOADED_SERVER_NAMES if name)

                def discovered_servers():
                    return tuple(DISCOVERED_SERVERS)

                def describe_server(name):
                    return _lookup_server(name)

                def list_loaded_server_metadata():
                    return tuple(AVAILABLE_SERVERS)

                def list_tools_sync(server=None):
                    if server is None:
                        raise MCPError("list_tools_sync(server) requires a server name")
                    info = _lookup_server(server)
                    tools = info.get("tools", ()) or ()
                    return tuple(tools)

                async def query_tool_docs(server, tool=None, detail="summary"):
                    payload = {"type": "query_tool_docs", "server": server}
                    if tool is not None:
                        payload["tool"] = tool
                    if detail is not None:
                        payload["detail"] = detail
                    response = await _rpc_call(payload)
                    if not response.get("success", True):
                        raise MCPError(response.get("error", "MCP request failed"))
                    docs = response.get("docs", [])
                    if tool is not None and isinstance(docs, list) and len(docs) == 1:
                        return docs[0]
                    return docs

                async def search_tool_docs(query, *, limit=5, detail="summary"):
                    payload = {"type": "search_tool_docs", "query": query}
                    if limit is not None:
                        payload["limit"] = limit
                    if detail is not None:
                        payload["detail"] = detail
                    response = await _rpc_call(payload)
                    if not response.get("success", True):
                        raise MCPError(response.get("error", "MCP request failed"))
                    return response.get("results", [])

                def query_tool_docs_sync(server, tool=None, detail="summary"):
                    info = _lookup_server(server)
                    detail_value = _normalise_detail(detail)
                    tools = info.get("tools", ()) or ()
                    if tool is None:
                        return [_format_tool_doc(info, tool_info, detail_value) for tool_info in tools]

                    if not isinstance(tool, str):
                        raise MCPError("'tool' must be a string when provided")
                    target = tool.lower()
                    for candidate in tools:
                        alias_value = str(candidate.get("alias", "")).lower()
                        name_value = str(candidate.get("name", "")).lower()
                        if target in {alias_value, name_value}:
                            return [_format_tool_doc(info, candidate, detail_value)]
                    raise MCPError(f"Tool {tool!r} not found for server {server}")

                def search_tool_docs_sync(query, *, limit=5, detail="summary"):
                    tokens = [token for token in str(query).lower().split() if token]
                    if not tokens:
                        return []
                    detail_value = _normalise_detail(detail)
                    try:
                        capped = max(1, min(20, int(limit)))
                    except Exception:
                        capped = 5
                    matches = []
                    for server_info in AVAILABLE_SERVERS:
                        tools = server_info.get("tools", ()) or ()
                        server_keywords = " ".join(
                            filter(
                                None,
                                (
                                    server_info.get("name"),
                                    server_info.get("alias"),
                                ),
                            )
                        ).lower()
                        for tool_info in tools:
                            haystack = " ".join(
                                filter(
                                    None,
                                    (
                                        server_keywords,
                                        tool_info.get("name"),
                                        tool_info.get("alias"),
                                        tool_info.get("description"),
                                    ),
                                )
                            ).lower()
                            if all(token in haystack for token in tokens):
                                matches.append(_format_tool_doc(server_info, tool_info, detail_value))
                                if len(matches) >= capped:
                                    return matches
                    return matches

                def capability_summary():
                    return _CAPABILITY_SUMMARY

                runtime_module.MCPError = MCPError
                runtime_module.call_tool = call_tool
                runtime_module.list_tools = list_tools
                runtime_module.list_servers = list_servers
                runtime_module.list_servers_sync = list_servers_sync
                runtime_module.discovered_servers = discovered_servers
                runtime_module.describe_server = describe_server
                runtime_module.list_loaded_server_metadata = list_loaded_server_metadata
                runtime_module.list_tools_sync = list_tools_sync
                runtime_module.query_tool_docs = query_tool_docs
                runtime_module.search_tool_docs = search_tool_docs
                runtime_module.query_tool_docs_sync = query_tool_docs_sync
                runtime_module.search_tool_docs_sync = search_tool_docs_sync
                runtime_module.capability_summary = capability_summary
                runtime_module.__all__ = [
                    "MCPError",
                    "call_tool",
                    "list_tools",
                    "list_tools_sync",
                    "list_servers",
                    "list_servers_sync",
                    "discovered_servers",
                    "describe_server",
                    "list_loaded_server_metadata",
                    "query_tool_docs_sync",
                    "query_tool_docs",
                    "search_tool_docs_sync",
                    "search_tool_docs",
                    "capability_summary",
                ]

                servers_module.__all__ = []

                def _make_tool_callable(server_name, tool_name):
                    async def _invoke(**kwargs):
                        return await call_tool(server_name, tool_name, kwargs)

                    return _invoke

                for server in AVAILABLE_SERVERS:
                    alias = server["alias"]
                    module_name = f"mcp.servers.{alias}"
                    server_module = types.ModuleType(module_name)
                    server_module.__doc__ = f"MCP server '{server['name']}' wrappers"
                    server_module.__all__ = []
                    tool_map = {}
                    for tool in server.get("tools", []):
                        tool_alias = tool["alias"]
                        summary = (tool.get("description") or "").strip() or f"MCP tool {tool['name']} from {server['name']}"
                        func = _make_tool_callable(server["name"], tool["name"])
                        func.__name__ = tool_alias
                        func.__doc__ = summary
                        setattr(server_module, tool_alias, func)
                        server_module.__all__.append(tool_alias)
                        tool_map[tool_alias] = tool
                    server_module.TOOLS = server.get("tools", [])
                    server_module.TOOL_MAP = tool_map
                    setattr(servers_module, alias, server_module)
                    sys.modules[module_name] = server_module
                    servers_module.__all__.append(alias)

                return runtime_module


            runtime_module = _install_mcp_modules()


            class _MCPProxy:
                def __init__(self, server_info):
                    self._server_name = server_info["name"]
                    self._tools = {tool["alias"]: tool for tool in server_info.get("tools", [])}

                async def list_tools(self):
                    response = await _rpc_call(
                        {
                            "type": "list_tools",
                            "server": self._server_name,
                        }
                    )
                    if not response.get("success", True):
                        raise RuntimeError(response.get("error", "MCP request failed"))
                    return response.get("tools", [])

                def __getattr__(self, tool_alias):
                    tool = self._tools.get(tool_alias)
                    target = tool.get("name") if tool else tool_alias
                    summary = (tool.get("description") if tool else "") or ""

                    async def _invoke(_target=target, **kwargs):
                        response = await _rpc_call(
                            {
                                "type": "call_tool",
                                "server": self._server_name,
                                "tool": _target,
                                "arguments": kwargs,
                            }
                        )
                        if not response.get("success", True):
                            raise RuntimeError(response.get("error", "MCP call failed"))
                        return response.get("result")

                    if summary:
                        _invoke.__doc__ = summary
                    _invoke.__name__ = tool_alias
                    return _invoke


            _SANDBOX_GLOBALS = globals()
            _SANDBOX_GLOBALS.setdefault("mcp", __import__("mcp"))
            LOADED_MCP_SERVERS = tuple(server["name"] for server in AVAILABLE_SERVERS)
            mcp_servers = {}
            for server in AVAILABLE_SERVERS:
                proxy = _MCPProxy(server)
                mcp_servers[server["name"]] = proxy
                _SANDBOX_GLOBALS[f"mcp_{server['alias']}"] = proxy

            _SANDBOX_GLOBALS.setdefault("mcp_servers", {}).update(mcp_servers)

            alias_map = {server["name"]: server["alias"] for server in AVAILABLE_SERVERS}


            async def _execute():
                await _ensure_reader()
                namespace = {"__name__": "__sandbox__"}
                namespace["mcp_servers"] = mcp_servers
                namespace["LOADED_MCP_SERVERS"] = LOADED_MCP_SERVERS
                namespace["mcp"] = __import__("mcp")
                for server_name, proxy in mcp_servers.items():
                    namespace[f"mcp_{alias_map[server_name]}"] = proxy
                flags = getattr(__import__("ast"), "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
                compiled = compile(CODE, "<sandbox>", "exec", flags=flags)
                result = eval(compiled, namespace, namespace)
                if inspect.isawaitable(result):
                    await result
                if _READER_TASK:
                    _READER_TASK.cancel()
                    with suppress(asyncio.CancelledError):
                        await _READER_TASK


            try:
                asyncio.run(_execute())
            except SystemExit:
                raise
            except Exception:
                traceback.print_exc()
                sys.exit(1)
            """
        ).lstrip()
        return (
            template.replace("__METADATA_JSON__", repr(metadata_json))
            .replace("__DISCOVERED_JSON__", repr(discovered_json))
            .replace("__CODE_LITERAL__", repr(code))
        )

    async def _run_runtime_command(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            self.runtime,
            *args,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text = stdout_bytes.decode(errors="replace")
        stderr_text = stderr_bytes.decode(errors="replace")
        assert process.returncode is not None
        return process.returncode, stdout_text, stderr_text

    async def _stop_runtime(self) -> None:
        runtime_name = os.path.basename(self.runtime)
        if "podman" not in runtime_name:
            return

        code, stdout_text, stderr_text = await self._run_runtime_command("machine", "stop")
        if code != 0:
            combined = f"{stdout_text}\n{stderr_text}".lower()
            if "already stopped" in combined or "is not running" in combined:
                return
            logger.debug("Failed to stop podman machine: %s", stderr_text.strip())

    async def _cancel_runtime_shutdown_timer(self) -> None:
        if not self._shutdown_task:
            return
        task = self._shutdown_task
        self._shutdown_task = None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _schedule_runtime_shutdown(self) -> None:
        if self.runtime_idle_timeout <= 0:
            return

        await self._cancel_runtime_shutdown_timer()

        async def _delayed_shutdown() -> None:
            try:
                await asyncio.sleep(self.runtime_idle_timeout)
                await self._stop_runtime()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - diagnostic fallback
                logger.debug("Runtime shutdown task failed", exc_info=True)

        self._shutdown_task = asyncio.create_task(_delayed_shutdown())

    async def _ensure_runtime_ready(self) -> None:
        async with self._runtime_check_lock:
            await self._cancel_runtime_shutdown_timer()
            runtime_name = os.path.basename(self.runtime)
            if "podman" not in runtime_name:
                return

            for _ in range(3):
                code, stdout_text, stderr_text = await self._run_runtime_command(
                    "info",
                    "--format",
                    "{{json .}}",
                )
                if code == 0:
                    return

                combined = f"{stdout_text}\n{stderr_text}".lower()
                needs_machine = any(
                    phrase in combined
                    for phrase in (
                        "cannot connect to podman",
                        "podman machine",
                        "run the podman machine",
                        "socket: connect",
                    )
                )
                if not needs_machine:
                    raise SandboxError(
                        "Container runtime is unavailable",
                        stdout=stdout_text,
                        stderr=stderr_text,
                    )

                start_code, start_stdout, start_stderr = await self._run_runtime_command("machine", "start")
                if start_code == 0:
                    continue

                start_combined = f"{start_stdout}\n{start_stderr}".lower()
                if "does not exist" in start_combined or "no such machine" in start_combined:
                    init_code, init_stdout, init_stderr = await self._run_runtime_command("machine", "init")
                    if init_code != 0:
                        raise SandboxError(
                            "Failed to initialize Podman machine",
                            stdout=init_stdout,
                            stderr=init_stderr,
                        )
                    # After init, loop will retry info/start sequence
                    continue

                raise SandboxError(
                    "Failed to start Podman machine",
                    stdout=start_stdout,
                    stderr=start_stderr,
                )

            raise SandboxError(
                "Unable to prepare Podman runtime",
                stdout="",
                stderr="Repeated podman machine start attempts failed",
            )

    async def execute(
        self,
        code: str,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        servers_metadata: Sequence[Dict[str, object]] = (),
        discovered_servers: Sequence[str] = (),
        container_env: Optional[Dict[str, str]] = None,
        volume_mounts: Optional[Sequence[str]] = None,
        host_dir: Optional[Path] = None,
        rpc_handler: Optional[Callable[[Dict[str, object]], Awaitable[Dict[str, object]]]] = None,
    ) -> SandboxResult:
        await self._ensure_runtime_ready()
        if host_dir is None:
            raise SandboxError("Sandbox host directory is not available")

        entrypoint_path = host_dir / "entrypoint.py"
        entrypoint_path.write_text(self._render_entrypoint(code, servers_metadata, discovered_servers))
        entrypoint_target = f"/ipc/{entrypoint_path.name}"

        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        cmd = self._base_cmd()
        if volume_mounts:
            for mount in volume_mounts:
                cmd.extend(["--volume", mount])
        if container_env:
            for key, value in container_env.items():
                cmd.extend(["--env", f"{key}={value}"])
        cmd.extend([self.image, "python3", "-u", entrypoint_target])

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
        )

        async def _handle_stdout() -> None:
            if not process.stdout:
                return
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode())
                except Exception:
                    stderr_chunks.append(line.decode(errors="replace"))
                    continue

                msg_type = message.get("type")
                if msg_type == "stdout":
                    stdout_chunks.append(message.get("data", ""))
                elif msg_type == "stderr":
                    stderr_chunks.append(message.get("data", ""))
                elif msg_type == "rpc_request":
                    if process.stdin is None:
                        continue
                    if rpc_handler is None:
                        response: Dict[str, object] = {"success": False, "error": "RPC handler unavailable"}
                    else:
                        try:
                            payload = message.get("payload", {})
                            response = await rpc_handler(payload if isinstance(payload, dict) else {})
                        except Exception as exc:
                            logger.debug("RPC handler failed", exc_info=True)
                            response = {"success": False, "error": str(exc)}
                    reply: Dict[str, object] = {
                        "type": "rpc_response",
                        "id": message.get("id"),
                        "success": response.get("success", True),
                        "payload": response,
                    }
                    if not reply["success"]:
                        reply["error"] = response.get("error", "RPC error")
                    try:
                        data = json.dumps(reply, separators=(",", ":")).encode("utf-8") + b"\n"
                        process.stdin.write(data)
                        await process.stdin.drain()
                    except Exception:
                        stderr_chunks.append("Failed to deliver RPC response\n")
                        break
                else:
                    stderr_chunks.append(json.dumps(message, separators=(",", ":")))

        async def _read_stderr() -> None:
            if not process.stderr:
                return
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk.decode(errors="replace"))

        stdout_task = asyncio.create_task(_handle_stdout())
        stderr_task = asyncio.create_task(_read_stderr())

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stdout_task
            with suppress(asyncio.CancelledError):
                await stderr_task
            raise SandboxTimeout(
                f"Execution timed out after {timeout}s",
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
            ) from exc
        finally:
            if process.stdin:
                process.stdin.close()
                with suppress(Exception):
                    await process.stdin.wait_closed()

        await stdout_task
        await stderr_task

        stdout_text = "".join(stdout_chunks)
        stderr_text = "".join(stderr_chunks)

        if process.returncode == 0:
            stderr_text = self._filter_runtime_stderr(stderr_text)

        try:
            exit_code = process.returncode
            assert exit_code is not None
            return SandboxResult(exit_code == 0, exit_code, stdout_text, stderr_text)
        finally:
            await self._schedule_runtime_shutdown()

    async def ensure_shared_directory(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        path_str = str(resolved)

        if path_str in self._shared_paths:
            return

        async with self._share_lock:
            if path_str in self._shared_paths:
                return

            shared = True
            runtime_name = os.path.basename(self.runtime)
            if "podman" in runtime_name:
                shared = await self._ensure_podman_volume_shared(resolved)

            if shared:
                self._shared_paths.add(path_str)

    async def _ensure_podman_volume_shared(self, path: Path) -> bool:
        share_spec = f"{path}:{path}"
        try:
            process = await asyncio.create_subprocess_exec(
                self.runtime,
                "machine",
                "set",
                "--rootful",
                "--volume",
                share_spec,
                stdout=aio_subprocess.PIPE,
                stderr=aio_subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.debug("Podman binary not found while ensuring volume share for %s", path)
            return False

        stdout_bytes, stderr_bytes = await process.communicate()
        stderr_text = stderr_bytes.decode(errors="replace")
        if process.returncode == 0:
            return True

        lower = stderr_text.lower()
        if "already exists" in lower or "would overwrite" in lower:
            return True

        logger.debug(
            "Failed to ensure podman shared volume for %s (exit %s): %s",
            path,
            process.returncode,
            stderr_text.strip() or stdout_bytes.decode(errors="replace").strip(),
        )
        return False

    def _filter_runtime_stderr(self, text: str) -> str:
        """Strip known runtime pull chatter so successful runs stay quiet."""

        if not text:
            return text

        runtime_name = os.path.basename(self.runtime).lower()
        if "podman" not in runtime_name:
            return text

        filtered_lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and any(stripped.startswith(prefix) for prefix in _PODMAN_PULL_PREFIXES):
                continue
            filtered_lines.append(line)

        return "\n".join(filtered_lines).strip("\n")


def detect_runtime(preferred: Optional[str] = None) -> str:
    """Return the first available container runtime."""

    candidates: List[Optional[str]] = []
    if preferred:
        candidates.append(preferred)
    if DEFAULT_RUNTIME and DEFAULT_RUNTIME not in candidates:
        candidates.append(DEFAULT_RUNTIME)
    candidates.extend(["podman", "docker"])

    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate

    raise SandboxError(
        "No container runtime found. Install podman or rootless docker and set "
        "MCP_BRIDGE_RUNTIME if multiple runtimes are available."
    )


class SandboxInvocation:
    """Context manager that prepares IPC resources for a sandbox invocation."""

    def __init__(self, bridge: "MCPBridge", active_servers: Sequence[str]) -> None:
        self.bridge = bridge
        self.active_servers = list(dict.fromkeys(active_servers))
        self._temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
        self.host_dir: Optional[Path] = None
        self.container_env: Dict[str, str] = {}
        self.volume_mounts: List[str] = []
        self.server_metadata: List[Dict[str, object]] = []
        self.allowed_servers: set[str] = set()
        self.discovered_servers: List[str] = []

    async def __aenter__(self) -> "SandboxInvocation":
        self.server_metadata = []
        for server_name in self.active_servers:
            metadata = await self.bridge.get_cached_server_metadata(server_name)
            self.server_metadata.append(metadata)
        self.allowed_servers = {
            str(meta.get("name")) for meta in self.server_metadata if isinstance(meta.get("name"), str)
        }
        self.discovered_servers = sorted(self.bridge.servers.keys())
        state_dir_env = os.environ.get("MCP_BRIDGE_STATE_DIR")
        if state_dir_env:
            base_dir = Path(state_dir_env).expanduser()
        else:
            base_dir = Path.cwd() / ".mcp-bridge"
        base_dir = base_dir.resolve()
        base_dir.mkdir(parents=True, exist_ok=True)

        ensure_share = getattr(self.bridge.sandbox, "ensure_shared_directory", None)
        if ensure_share:
            await ensure_share(base_dir)

        self._temp_dir = tempfile.TemporaryDirectory(prefix="mcp-bridge-ipc-", dir=str(base_dir))
        host_dir = Path(self._temp_dir.name)
        os.chmod(host_dir, 0o755)
        self.host_dir = host_dir

        self.volume_mounts.append(f"{host_dir}:/ipc:rw")

        self.container_env["MCP_AVAILABLE_SERVERS"] = json.dumps(
            self.server_metadata,
            separators=(",", ":"),
        )
        self.container_env["MCP_DISCOVERED_SERVERS"] = json.dumps(
            self.discovered_servers,
            separators=(",", ":"),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._temp_dir:
            self._temp_dir.cleanup()

    async def handle_rpc(self, request: Dict[str, object]) -> Dict[str, object]:
        req_type = request.get("type")
        if req_type == "list_servers":
            return {
                "success": True,
                "servers": sorted(self.allowed_servers),
            }

        if req_type == "query_tool_docs":
            server = request.get("server")
            if not isinstance(server, str) or server not in self.allowed_servers:
                return {
                    "success": False,
                    "error": f"Server {server!r} is not available",
                }
            tool = request.get("tool")
            if tool is not None and not isinstance(tool, str):
                return {
                    "success": False,
                    "error": "'tool' must be a string when provided",
                }
            detail = request.get("detail", "summary")
            try:
                docs = await self.bridge.get_tool_docs(server, tool=tool, detail=detail)
            except SandboxError as exc:
                return {"success": False, "error": str(exc)}
            return {"success": True, "docs": docs}

        if req_type == "search_tool_docs":
            query = request.get("query")
            if not isinstance(query, str) or not query.strip():
                return {
                    "success": False,
                    "error": "Missing 'query' value",
                }
            limit = request.get("limit", 5)
            if not isinstance(limit, int):
                return {
                    "success": False,
                    "error": "'limit' must be an integer",
                }
            detail = request.get("detail", "summary")
            try:
                results = await self.bridge.search_tool_docs(
                    query,
                    allowed_servers=sorted(self.allowed_servers),
                    limit=limit,
                    detail=detail,
                )
            except SandboxError as exc:
                return {"success": False, "error": str(exc)}
            return {"success": True, "results": results}

        if req_type not in {"list_tools", "call_tool"}:
            return {
                "success": False,
                "error": f"Unknown RPC type: {req_type}",
            }

        server = request.get("server")
        if not isinstance(server, str) or server not in self.allowed_servers:
            return {
                "success": False,
                "error": f"Server {server!r} is not available",
            }

        client = self.bridge.clients.get(server)
        if not client:
            return {
                "success": False,
                "error": f"Server {server} is not loaded",
            }

        try:
            if req_type == "list_tools":
                client_obj = cast(ClientLike, client)
                tools = await client_obj.list_tools()
                return {"success": True, "tools": tools}

            tool_name = request.get("tool")
            arguments = request.get("arguments", {})
            if not isinstance(tool_name, str):
                return {"success": False, "error": "Missing tool name"}
            if not isinstance(arguments, dict):
                return {"success": False, "error": "Arguments must be an object"}

            arguments = cast(Dict[str, object], arguments)
            client_obj = cast(ClientLike, client)
            result = await client_obj.call_tool(tool_name, arguments)
            return {"success": True, "result": result}
        except Exception as exc:  # pragma: no cover
            logger.debug("MCP proxy call failed", exc_info=True)
            return {"success": False, "error": str(exc)}


class MCPBridge:
    """Expose the secure sandbox as an MCP tool with MCP proxying."""

    def __init__(self, sandbox: Optional[object] = None) -> None:
        self.sandbox = sandbox or RootlessContainerSandbox()
        self.servers: Dict[str, MCPServerInfo] = {}
        self.clients: Dict[str, object] = {}
        self.loaded_servers: set[str] = set()
        self._aliases: Dict[str, str] = {}
        self._discovered = False
        self._server_metadata_cache: Dict[str, Dict[str, object]] = {}
        self._server_docs_cache: Dict[str, Dict[str, object]] = {}
        self._search_index: List[Dict[str, object]] = []
        self._search_index_dirty = False

    async def discover_servers(self) -> None:
        if self._discovered:
            return
        self._discovered = True

        for config_path in CLAUDE_CONFIG_PATHS:
            if not config_path.exists():
                continue
            try:
                with config_path.open() as fh:
                    config = json.load(fh)
                for name, value in config.get("mcpServers", {}).items():
                    info = self._parse_server_config(name, value)
                    if info:
                        self.servers[name] = info
                        logger.info("Found MCP server %s in %s", name, config_path)
            except Exception as exc:  # pragma: no cover
                logger.warning("Failed to read %s: %s", config_path, exc)

        for config_dir in CONFIG_DIRS:
            if not config_dir.exists():
                continue
            for config_file in config_dir.glob("*.json"):
                try:
                    with config_file.open() as fh:
                        config = json.load(fh)
                    for name, value in config.get("mcpServers", {}).items():
                        if name in self.servers:
                            continue
                        info = self._parse_server_config(name, value)
                        if info:
                            self.servers[name] = info
                            logger.info("Found MCP server %s in %s", name, config_file)
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to read %s: %s", config_file, exc)

        logger.info("Discovered %d MCP servers", len(self.servers))

    def _parse_server_config(self, name: str, raw: Dict[str, object]) -> Optional[MCPServerInfo]:
        command = raw.get("command")
        if not isinstance(command, str):
            return None
        args = raw.get("args", [])
        if not isinstance(args, list):
            args = []
        env = raw.get("env", {})
        if not isinstance(env, dict):
            env = {}
        str_env = {str(k): str(v) for k, v in env.items()}
        str_args = [str(arg) for arg in args]
        cwd_raw = raw.get("cwd")
        cwd_str: Optional[str] = None
        if isinstance(cwd_raw, (str, Path)):
            cwd_str = str(cwd_raw)
        return MCPServerInfo(name=name, command=command, args=str_args, env=str_env, cwd=cwd_str)

    async def load_server(self, server_name: str) -> None:
        if server_name in self.loaded_servers:
            return
        info = self.servers.get(server_name)
        if not info:
            raise SandboxError(f"Unknown MCP server: {server_name}")

        # Validate cwd if provided - warn, but do not fail startup
        if info.cwd:
            try:
                path = Path(info.cwd)
                if not path.exists():
                    logger.warning("Configured cwd for MCP server %s does not exist: %s", server_name, info.cwd)
            except Exception:
                logger.debug("Failed to check cwd for server %s: %s", server_name, info.cwd, exc_info=True)

        client = PersistentMCPClient(info)
        await client.start()
        self.clients[server_name] = client
        self.loaded_servers.add(server_name)
        logger.info("Loaded MCP server %s", server_name)
        self._server_metadata_cache.pop(server_name, None)
        self._server_docs_cache.pop(server_name, None)
        self._search_index_dirty = True

    def _alias_for(self, name: str) -> str:
        if name in self._aliases:
            return self._aliases[name]
        base = re.sub(r"[^a-z0-9_]+", "_", name.lower()) or "server"
        if base[0].isdigit():
            base = f"_{base}"
        alias = base
        suffix = 1
        used = set(self._aliases.values())
        while alias in used:
            suffix += 1
            alias = f"{base}_{suffix}"
        self._aliases[name] = alias
        return alias

    async def _ensure_server_metadata(self, server_name: str) -> None:
        if server_name in self._server_metadata_cache:
            return

        client = self.clients.get(server_name)
        if not client:
            raise SandboxError(f"Server {server_name} is not loaded")

        client_obj = cast(ClientLike, client)
        tool_specs = await client_obj.list_tools()
        alias = self._alias_for(server_name)
        alias_counts: Dict[str, int] = {}
        tools: List[Dict[str, object]] = []
        doc_entries: List[Dict[str, object]] = []
        identifier_index: Dict[str, Dict[str, object]] = {}

        for spec in tool_specs:
            raw_name = str(spec.get("name") or "tool")
            base_alias = _sanitize_identifier(raw_name, default="tool")
            alias_counts[base_alias] = alias_counts.get(base_alias, 0) + 1
            count = alias_counts[base_alias]
            tool_alias = base_alias if count == 1 else f"{base_alias}_{count}"

            input_schema = spec.get("input_schema") or spec.get("inputSchema")
            description = str(spec.get("description") or "").strip()

            tool_payload = {
                "name": raw_name,
                "alias": tool_alias,
                "description": description,
                "input_schema": input_schema,
            }
            tools.append(tool_payload)

            keywords = " ".join(
                filter(
                    None,
                    {
                        server_name,
                        alias,
                        raw_name,
                        tool_alias,
                        description,
                    },
                )
            ).lower()

            doc_entry = {
                "name": raw_name,
                "alias": tool_alias,
                "description": description,
                "input_schema": input_schema,
                "keywords": keywords,
            }
            doc_entries.append(doc_entry)
            identifier_index[tool_alias.lower()] = doc_entry
            identifier_index[raw_name.lower()] = doc_entry

        server_obj = self.servers.get(server_name)
        cwd_value = str(server_obj.cwd) if server_obj and getattr(server_obj, "cwd", None) else None
        metadata = {
            "name": server_name,
            "alias": alias,
            "tools": tools,
            "cwd": cwd_value,
        }

        self._server_metadata_cache[server_name] = metadata
        self._server_docs_cache[server_name] = {
            "name": server_name,
            "alias": alias,
            "tools": doc_entries,
            "identifier_index": identifier_index,
        }
        self._search_index_dirty = True

    async def get_cached_server_metadata(self, server_name: str) -> Dict[str, object]:
        await self._ensure_server_metadata(server_name)
        return copy.deepcopy(self._server_metadata_cache[server_name])

    @staticmethod
    def _normalise_detail(value: object) -> str:
        detail = str(value).lower() if value is not None else "summary"
        return detail if detail in {"summary", "full"} else "summary"

    @staticmethod
    def _format_tool_doc(
        server_name: str,
        server_alias: str,
        info: Dict[str, object],
        detail: str,
    ) -> Dict[str, object]:
        doc: Dict[str, object] = {
            "server": server_name,
            "serverAlias": server_alias,
            "tool": info.get("name"),
            "toolAlias": info.get("alias"),
        }
        description = info.get("description")
        if description:
            doc["description"] = description
        if detail == "full" and info.get("input_schema") is not None:
            doc["inputSchema"] = info.get("input_schema")
        return doc

    async def get_tool_docs(
        self,
        server_name: str,
        *,
        tool: Optional[str] = None,
        detail: object = "summary",
    ) -> List[Dict[str, object]]:
        await self._ensure_server_metadata(server_name)
        cache_entry = self._server_docs_cache.get(server_name)
        if not cache_entry:
            raise SandboxError(f"Documentation unavailable for server {server_name}")

        detail_value = self._normalise_detail(detail)
        server_alias = str(cache_entry.get("alias", ""))
        docs: List[Dict[str, object]] = []

        if tool is not None:
            if not isinstance(tool, str):
                raise SandboxError("'tool' must be a string when provided")
            identifier_map_raw = cache_entry.get("identifier_index", {})
            identifier_map: Dict[str, Dict[str, object]] = {}
            if isinstance(identifier_map_raw, dict):
                identifier_map = cast(Dict[str, Dict[str, object]], identifier_map_raw)
            match = identifier_map.get(tool.lower())
            if not match:
                raise SandboxError(f"Tool {tool!r} not found for server {server_name}")
            docs.append(self._format_tool_doc(server_name, server_alias, cast(Dict[str, object], match), detail_value))
            return docs

        tools_raw = cache_entry.get("tools", [])
        if not isinstance(tools_raw, (list, tuple)):
            tools_raw = []
        for info_raw in tools_raw:
            info = cast(Dict[str, object], info_raw)
            docs.append(self._format_tool_doc(server_name, server_alias, info, detail_value))
        return docs

    def _ensure_search_index(self) -> None:
        if not self._search_index_dirty:
            return

        entries: List[Dict[str, object]] = []
        for server_name, cache_entry in self._server_docs_cache.items():
            server_alias = str(cache_entry.get("alias", ""))
            tools_raw = cache_entry.get("tools", [])
            if not isinstance(tools_raw, (list, tuple)):
                continue
            for info_raw in tools_raw:
                info = cast(Dict[str, object], info_raw)
                entries.append(
                    {
                        "server": server_name,
                        "server_alias": server_alias,
                        "info": info,
                        "keywords": str(info.get("keywords", "")),
                    }
                )

        self._search_index = entries
        self._search_index_dirty = False

    async def search_tool_docs(
        self,
        query: str,
        *,
        allowed_servers: Sequence[str],
        limit: int = 5,
        detail: object = "summary",
    ) -> List[Dict[str, object]]:
        if not query.strip():
            return []

        for server_name in allowed_servers:
            await self._ensure_server_metadata(server_name)

        self._ensure_search_index()
        tokens = [token for token in query.lower().split() if token]
        if not tokens:
            return []

        detail_value = self._normalise_detail(detail)
        allowed = set(allowed_servers)
        matches: List[Dict[str, object]] = []

        for entry in self._search_index:
            if entry.get("server") not in allowed:
                continue
            keywords = str(entry.get("keywords", ""))
            if all(token in keywords for token in tokens):
                info_raw = entry.get("info", {})
                info = cast(Dict[str, object], info_raw)
                matches.append(
                    self._format_tool_doc(
                        str(entry.get("server")),
                        str(entry.get("server_alias", "")),
                        info,
                        detail_value,
                    )
                )

        capped = max(1, min(20, limit))
        return matches[:capped]

    async def execute_code(
        self,
        code: str,
        servers: Optional[Sequence[str]] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> SandboxResult:
        await self.discover_servers()
        request_timeout = max(1, min(MAX_TIMEOUT, timeout))
        requested_servers = list(dict.fromkeys(servers or []))

        for server_name in requested_servers:
            await self.load_server(server_name)

        async with SandboxInvocation(self, requested_servers) as invocation:
            sandbox_obj = cast(SandboxLike, self.sandbox)
            result = await sandbox_obj.execute(
                code,
                timeout=request_timeout,
                servers_metadata=invocation.server_metadata,
                discovered_servers=invocation.discovered_servers,
                container_env=invocation.container_env,
                volume_mounts=invocation.volume_mounts,
                host_dir=invocation.host_dir,
                rpc_handler=invocation.handle_rpc,
            )

        if not result.success:
            raise SandboxError(
                f"Sandbox exited with code {result.exit_code}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result


bridge = MCPBridge()
app = Server(BRIDGE_NAME)


@app.list_tools()
async def list_tools() -> List[Tool]:
    return [
        Tool(
            name="run_python",
            description=(
                "Execute Python code inside a rootless container sandbox. "
                "Use the optional 'servers' array to load MCP servers for this execution."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python source code to execute. Call runtime.capability_summary() inside the sandbox for this digest. "
                            f"{SANDBOX_HELPERS_SUMMARY}"
                        ),
                    },
                    "servers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of MCP servers to make available as mcp_<name> proxies"
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_TIMEOUT,
                        "default": DEFAULT_TIMEOUT,
                        "description": "Execution timeout in seconds",
                    },
                },
                "required": ["code"],
            },
        )
    ]


@app.list_resources()
async def list_resources() -> List[Resource]:
    return [_build_capability_resource()]


@app.read_resource()
async def read_resource(uri: str) -> str:
    uri_str = str(uri)
    if uri_str != CAPABILITY_RESOURCE_URI:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown resource: {uri_str}",
            )
        )
    return _CAPABILITY_RESOURCE_TEXT


@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, object]) -> CallToolResult:
    if name != "run_python":
        return _build_tool_response(
            status="error",
            summary=f"Unknown tool: {name}",
            error=f"Unknown tool: {name}",
        )

    code = arguments.get("code")
    if not isinstance(code, str) or not code.strip():
        return _build_tool_response(
            status="validation_error",
            summary="Missing 'code' argument",
            error="Missing 'code' argument",
        )

    servers = arguments.get("servers", [])
    if not isinstance(servers, list):
        return _build_tool_response(
            status="validation_error",
            summary="'servers' must be a list",
            error="'servers' must be a list",
        )
    server_list = [str(server) for server in servers]

    timeout_value = arguments.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout_value, int):
        return _build_tool_response(
            status="validation_error",
            summary="'timeout' must be an integer",
            error="'timeout' must be an integer",
        )
    timeout_value = max(1, min(MAX_TIMEOUT, timeout_value))

    try:
        result = await bridge.execute_code(code, server_list, timeout_value)
        summary = "Success"
        if not result.stdout and not result.stderr:
            summary = "Success (no output)"
        return _build_tool_response(
            status="success",
            summary=summary,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            servers=server_list,
        )
    except SandboxTimeout as exc:
        summary = f"Timeout: execution exceeded {timeout_value}s"
        return _build_tool_response(
            status="timeout",
            summary=summary,
            stdout=exc.stdout,
            stderr=exc.stderr,
            servers=server_list,
            error=str(exc),
            timeout_seconds=timeout_value,
        )
    except SandboxError as exc:
        summary = f"Sandbox error: {exc}"
        return _build_tool_response(
            status="error",
            summary=summary,
            stdout=exc.stdout,
            stderr=exc.stderr,
            servers=server_list,
            error=str(exc),
        )
    except Exception as exc:  # pragma: no cover
        logger.error("Unexpected failure", exc_info=True)
        return _build_tool_response(
            status="error",
            summary="Unexpected failure",
            error=str(exc),
        )


async def main() -> None:
    logging.basicConfig(level=os.environ.get("MCP_BRIDGE_LOG_LEVEL", "INFO"))
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
