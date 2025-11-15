import unittest
from typing import Any, Dict, List, cast

from mcp_server_code_execution_mode import MCPBridge, MCPServerInfo, SandboxInvocation


class _DummySandbox:
    async def ensure_shared_directory(self, _path):
        return None


class _FakeClient:
    def __init__(self, tools):
        self._tools = tools

    async def list_tools(self):
        return self._tools


class ToolDocsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bridge = MCPBridge(sandbox=_DummySandbox())
        self.bridge.servers["demo-server"] = MCPServerInfo(
            name="demo-server",
            command="fake",
            args=[],
            env={},
        )
        self.bridge.clients["demo-server"] = _FakeClient(
            [
                {
                    "name": "list_things",
                    "description": "List available things",
                    "inputSchema": {"type": "object"},
                },
                {
                    "name": "get_thing",
                    "description": "Retrieve a single thing",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
            ]
        )
        self.bridge.loaded_servers.add("demo-server")

    async def test_get_tool_docs_summary_and_full(self) -> None:
        summary_docs = await self.bridge.get_tool_docs("demo-server")
        self.assertEqual(len(summary_docs), 2)
        self.assertEqual(summary_docs[0]["server"], "demo-server")
        self.assertIn("description", summary_docs[0])
        full_doc = await self.bridge.get_tool_docs("demo-server", tool="get_thing", detail="full")
        self.assertEqual(len(full_doc), 1)
        self.assertIn("inputSchema", full_doc[0])

    async def test_search_tool_docs(self) -> None:
        results = await self.bridge.search_tool_docs(
            "retrieve",
            allowed_servers=["demo-server"],
            limit=5,
            detail="summary",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "get_thing")
        full_results = await self.bridge.search_tool_docs(
            "thing",
            allowed_servers=["demo-server"],
            limit=1,
            detail="full",
        )
        self.assertEqual(len(full_results), 1)
        self.assertIn("inputSchema", full_results[0])

    async def test_rpc_handlers_expose_docs(self) -> None:
        async with SandboxInvocation(self.bridge, ["demo-server"]) as invocation:
            query_response = await invocation.handle_rpc(
                {
                    "type": "query_tool_docs",
                    "server": "demo-server",
                    "tool": "list_things",
                    "detail": "summary",
                }
            )
            self.assertTrue(query_response["success"])
            docs = cast(List[Dict[str, Any]], query_response.get("docs", []))
            self.assertEqual(len(docs), 1)
            search_response = await invocation.handle_rpc(
                {
                    "type": "search_tool_docs",
                    "query": "list",
                    "limit": 2,
                    "detail": "summary",
                }
            )
            self.assertTrue(search_response["success"])
            results = cast(List[Dict[str, Any]], search_response.get("results", []))
            self.assertGreaterEqual(len(results), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
