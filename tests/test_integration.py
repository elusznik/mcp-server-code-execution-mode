import asyncio
import json
import logging
import os
import sys
import tempfile
import traceback
import unittest
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Awaitable, Callable, ClassVar, Dict, List, Optional, Sequence, cast
from unittest import mock

import anyio
import mcp.types as mcp_types

import mcp_server_code_execution_mode as bridge_module
from mcp_server_code_execution_mode import SandboxError, SandboxResult, SandboxTimeout


class InProcessSandbox:
    async def execute(
        self,
        code: str,
        *,
        timeout: int,
        servers_metadata: Sequence[Dict[str, object]],
        discovered_servers: Sequence[str] = (),
        container_env: Optional[Dict[str, str]] = None,
        volume_mounts: Optional[Sequence[str]] = None,
        host_dir: Optional[Path] = None,
        rpc_handler: Optional[
            Callable[[Dict[str, object]], Awaitable[Dict[str, object]]]
        ] = None,
    ) -> SandboxResult:
        async def _rpc_call(payload: Dict[str, object]) -> Dict[str, object]:
            if not isinstance(payload, dict):
                raise RuntimeError("RPC payload must be a dictionary")
            if rpc_handler is None:
                raise RuntimeError("MCP RPC handler is not available")
            return await rpc_handler(payload)

        class _MCPProxy:
            def __init__(self, server_info: Dict[str, object]):
                self._server_name = str(server_info.get("name"))
                raw_tools = server_info.get("tools", [])
                if isinstance(raw_tools, (list, tuple)):
                    tools = list(raw_tools)
                else:
                    tools = []
                self._tools = {str(tool["alias"]): tool for tool in tools}

            async def list_tools(self):
                response = await _rpc_call(
                    {"type": "list_tools", "server": self._server_name}
                )
                if not response.get("success"):
                    raise RuntimeError(response.get("error", "Failed to list tools"))
                return response.get("tools", [])

            def __getattr__(self, alias: str):
                tool = self._tools.get(alias)
                target = tool.get("name") if tool else alias

                async def _invoke(**kwargs):
                    response = await _rpc_call(
                        {
                            "type": "call_tool",
                            "server": self._server_name,
                            "tool": target,
                            "arguments": kwargs,
                        }
                    )
                    if not response.get("success"):
                        raise RuntimeError(response.get("error", "MCP call failed"))
                    return response.get("result")

                return _invoke

        alias_map = {
            str(server["name"]): str(server["alias"]) for server in servers_metadata
        }
        mcp_servers = {
            str(server["name"]): _MCPProxy(server) for server in servers_metadata
        }

        namespace = {"__name__": "__sandbox__", "mcp_servers": mcp_servers}
        for server_name, proxy in mcp_servers.items():
            alias = alias_map[server_name]
            namespace[f"mcp_{alias}"] = proxy

        flags = getattr(__import__("ast"), "PyCF_ALLOW_TOP_LEVEL_AWAIT", 0)
        compiled = compile(code, "<sandbox>", "exec", flags=flags)

        stdout_buf = StringIO()
        stderr_buf = StringIO()

        async def _run_user_code():
            result = eval(compiled, namespace, namespace)
            if asyncio.iscoroutine(result):
                await result

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                await asyncio.wait_for(_run_user_code(), timeout)
            return SandboxResult(True, 0, stdout_buf.getvalue(), stderr_buf.getvalue())
        except asyncio.TimeoutError as exc:
            raise SandboxTimeout(
                f"Execution timed out after {timeout}s",
                stdout=stdout_buf.getvalue(),
                stderr=stderr_buf.getvalue(),
            ) from exc
        except SystemExit as exc:  # pragma: no cover - mirrors container behaviour
            code_val = exc.code if isinstance(exc.code, int) else 1
            return SandboxResult(
                code_val == 0, code_val, stdout_buf.getvalue(), stderr_buf.getvalue()
            )
        except Exception:  # pragma: no cover - diagnostic parity with container path
            traceback.print_exc(file=stderr_buf)
            return SandboxResult(False, 1, stdout_buf.getvalue(), stderr_buf.getvalue())


class StubIntegrationTests(unittest.IsolatedAsyncioTestCase):
    _original_config_sources: ClassVar[List[object]] = []

    @classmethod
    def setUpClass(cls) -> None:
        cls._original_config_sources = list(bridge_module.CONFIG_SOURCES)

    @classmethod
    def tearDownClass(cls) -> None:
        bridge_module.CONFIG_SOURCES[:] = cls._original_config_sources

    async def asyncSetUp(self) -> None:
        self._config_dir = tempfile.TemporaryDirectory()
        self._state_dir = tempfile.TemporaryDirectory()
        self._original_state_dir = os.environ.get("MCP_BRIDGE_STATE_DIR")
        os.environ["MCP_BRIDGE_STATE_DIR"] = self._state_dir.name

        # Set up a single directory source for the test
        from mcp_server_code_execution_mode import ConfigSource

        bridge_module.CONFIG_SOURCES[:] = [
            ConfigSource(Path(self._config_dir.name), "directory", name="Test Dir")
        ]

        stub_path = Path(__file__).resolve().parent / "stub_mcp_server.py"
        config = {
            "mcpServers": {
                "stub": {
                    "command": sys.executable,
                    "args": [str(stub_path)],
                    "env": {},
                }
            }
        }
        Path(self._config_dir.name, "stub_server.json").write_text(json.dumps(config))

        self.bridge = bridge_module.MCPBridge(sandbox=InProcessSandbox())

    async def asyncTearDown(self) -> None:
        try:
            for client in self.bridge.clients.values():
                if getattr(client, "_session", None) is None:
                    continue
                try:
                    client_obj = cast(bridge_module.ClientLike, client)
                    await client_obj.stop()
                except Exception:  # pragma: no cover - diagnostic aid
                    traceback.print_exc()
        finally:
            if self._original_state_dir is None:
                os.environ.pop("MCP_BRIDGE_STATE_DIR", None)
            else:
                os.environ["MCP_BRIDGE_STATE_DIR"] = self._original_state_dir
            self._config_dir.cleanup()
            self._state_dir.cleanup()

    async def test_stub_echo_tool(self) -> None:
        code = "\n".join(
            [
                "result = await mcp_stub.echo(message='hello world')",
                "assert result['content'][0]['text'] == 'hello world'",
            ]
        )

        try:
            result = await self.bridge.execute_code(
                code,
                servers=["stub"],
                timeout=30,
            )
        except SandboxError as exc:  # pragma: no cover - diagnostic aid
            self.fail(
                "SandboxError while executing integration code:\n"
                f"STDOUT:\n{exc.stdout}\n"
                f"STDERR:\n{exc.stderr}\n"
            )

        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

        client = self.bridge.clients.get("stub")
        if client:
            client_obj = cast(bridge_module.ClientLike, client)
            await client_obj.stop()

    async def test_discover_opencode_config_file(self) -> None:
        opencode_path = Path(self._config_dir.name, "opencode_config.json")
        config = {
            "mcpServers": {
                "opencode-server": {
                    "command": sys.executable,
                    "args": [],
                    "env": {},
                }
            }
        }
        Path(self._config_dir.name, "opencode_config.json").write_text(
            json.dumps(config)
        )

        # Add the explicit file source
        from mcp_server_code_execution_mode import ConfigSource

        bridge_module.CONFIG_SOURCES.append(
            ConfigSource(opencode_path, "file", name="OpenCode Test")
        )

        await self.bridge.discover_servers()
        self.assertIn("opencode-server", self.bridge.servers)

    async def test_blank_line_parse_exception_is_ignored(self) -> None:
        # Write a server config for a server that will send a blank-line parse exception

        broken_config = {
            "mcpServers": {
                "broken": {
                    "command": sys.executable,
                    "args": [],
                    "env": {},
                }
            }
        }
        Path(self._config_dir.name, "broken_server.json").write_text(
            json.dumps(broken_config)
        )

        @asynccontextmanager
        async def fake_stdio_client(server, errlog=None):
            # Create memory streams
            # Use a single-slot buffer so the send below doesn't block before the
            # consumer attaches (zero-buffer streams deadlock during __aenter__).
            read_send, read_recv = anyio.create_memory_object_stream(1)
            write_send, write_recv = anyio.create_memory_object_stream(1)

            # Simulate a JSON parse exception for a blank newline as produced by pydantic
            exc = Exception(
                "1 validation error for JSONRPCMessage\n  Invalid JSON: EOF while parsing a value at line 2 column 0 [type=json_invalid, input_value='\\n', input_type=str]"
            )
            await read_send.send(exc)

            try:
                yield read_recv, write_send
            finally:
                await read_send.aclose()
                await write_send.aclose()

        # Dummy initializer to bypass actual init sequence
        class DummyInitResult:
            protocolVersion = mcp_types.LATEST_PROTOCOL_VERSION
            capabilities = mcp_types.ServerCapabilities()

        async def fake_init(self):
            return DummyInitResult()

        with mock.patch.object(bridge_module, "stdio_client", fake_stdio_client):
            with mock.patch.object(
                bridge_module.ClientSession, "initialize", fake_init
            ):
                # Capture server-side logs for 'mcp.server.lowlevel.server'
                log_stream = StringIO()
                handler = logging.StreamHandler(log_stream)
                server_logger = logging.getLogger("mcp.server.lowlevel.server")
                server_logger.addHandler(handler)
                try:
                    await self.bridge.discover_servers()
                    await self.bridge.load_server("broken")
                finally:
                    server_logger.removeHandler(handler)

                logs = log_stream.getvalue()
                self.assertNotIn("Received exception from stream", logs)
                self.assertNotIn("Internal Server Error", logs)


if __name__ == "__main__":
    unittest.main()
