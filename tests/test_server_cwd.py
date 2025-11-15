import unittest
from contextlib import asynccontextmanager
from typing import Any, cast
from unittest import mock

import anyio
import mcp.types as mcp_types

import mcp_server_code_execution_mode as bridge_module
from mcp_server_code_execution_mode import MCPServerInfo


class ServerCwdTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_start_uses_configured_cwd(self) -> None:
        # Arrange: create a bridge and a server config with a cwd
        bridge = bridge_module.bridge
        server_name = "serena-test"
        server_cwd = "/tmp/serena-workdir"
        bridge.servers[server_name] = MCPServerInfo(
            name=server_name,
            command="true",
            args=[],
            env={},
            cwd=server_cwd,
        )

        captured = {}

        @asynccontextmanager
        async def fake_stdio_client(server: Any):
            # Capture the provided parameters
            captured["params"] = server
            # Create memory streams compatible with ClientSession
            send, recv = anyio.create_memory_object_stream(0)
            recv_send, recv_recv = anyio.create_memory_object_stream(0)
            # The stdio client yields (read_stream, write_stream)
            try:
                yield (recv_recv, send)
            finally:
                await send.aclose()
                await recv_recv.aclose()

        # Patch stdio_client to our fake implementation
        # Patch ClientSession.initialize to a trivial initializer
        class DummyInitResult:
            protocolVersion = mcp_types.LATEST_PROTOCOL_VERSION
            capabilities = mcp_types.ServerCapabilities()

        async def fake_init(self):
            return DummyInitResult()
        with mock.patch.object(bridge_module, "stdio_client", fake_stdio_client):
            with mock.patch.object(bridge_module.ClientSession, "initialize", fake_init):
                # Act
                await bridge.load_server(server_name)

        # Assert
        self.assertIn("params", captured)
        params = captured["params"]
        # StdioServerParameters may provide cwd as a Path or str - convert to str
        self.assertEqual(str(params.cwd), server_cwd)

        # Cleanup
        if server_name in bridge.clients:
            client_obj = cast(bridge_module.ClientLike, bridge.clients[server_name])
            await client_obj.stop()
            del bridge.clients[server_name]
        if server_name in bridge.servers:
            del bridge.servers[server_name]
        if server_name in bridge.loaded_servers:
            bridge.loaded_servers.remove(server_name)

    async def test_load_server_warns_if_cwd_missing(self) -> None:
        bridge = bridge_module.bridge
        server_name = "serena-missing-cwd"
        server_cwd = "/nonexistent/foobar-b7c9"
        bridge.servers[server_name] = MCPServerInfo(
            name=server_name,
            command="true",
            args=[],
            env={},
            cwd=server_cwd,
        )

        captured = {}

        @asynccontextmanager
        async def fake_stdio_client(server: Any):
            captured["params"] = server
            send, recv = anyio.create_memory_object_stream(0)
            recv_send, recv_recv = anyio.create_memory_object_stream(0)
            try:
                yield (recv_recv, send)
            finally:
                await send.aclose()
                await recv_recv.aclose()

        class DummyInitResult:
            protocolVersion = mcp_types.LATEST_PROTOCOL_VERSION
            capabilities = mcp_types.ServerCapabilities()

        async def fake_init(self):
            return DummyInitResult()

        with mock.patch.object(bridge_module, "stdio_client", fake_stdio_client):
            with mock.patch.object(bridge_module.ClientSession, "initialize", fake_init):
                with mock.patch.object(bridge_module, "logger") as fake_logger:
                    await bridge.load_server(server_name)
                    # logger.warning should be called once because cwd doesn't exist
                    self.assertTrue(fake_logger.warning.called)

        # Cleanup
        if server_name in bridge.clients:
            client_obj = cast(bridge_module.ClientLike, bridge.clients[server_name])
            await client_obj.stop()
            del bridge.clients[server_name]
        if server_name in bridge.servers:
            del bridge.servers[server_name]
        if server_name in bridge.loaded_servers:
            bridge.loaded_servers.remove(server_name)

    async def test_get_cached_server_metadata_includes_cwd(self) -> None:
        bridge = bridge_module.bridge
        server_name = "serena-test-meta"
        server_cwd = "/tmp/serena-meta"
        bridge.servers[server_name] = MCPServerInfo(
            name=server_name,
            command="true",
            args=[],
            env={},
            cwd=server_cwd,
        )

        class FakeClient:
            async def start(self):
                return None

            async def list_tools(self):
                return []

        # Inject a fake client and ensure metadata gets built
        bridge.clients[server_name] = FakeClient()  # type: ignore[assignment]
        await bridge._ensure_server_metadata(server_name)
        metadata = await bridge.get_cached_server_metadata(server_name)

        self.assertIsNotNone(metadata)
        self.assertIn("cwd", metadata)
        self.assertEqual(metadata["cwd"], server_cwd)

        # Cleanup
        del bridge.clients[server_name]
        del bridge.servers[server_name]


if __name__ == "__main__":  # pragma: no cover - convenience for direct runs
    unittest.main()
