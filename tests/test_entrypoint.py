import io
import json
import os
import sys
import unittest
from typing import Dict, List

from mcp_server_code_execution_mode import RootlessContainerSandbox


class EntryPointGenerationTests(unittest.TestCase):
    def test_generates_runtime_modules(self) -> None:
        metadata = [
            {
                "name": "demo-server",
                "alias": "demo_server",
                "tools": [
                    {
                        "name": "list_things",
                        "alias": "list_things",
                        "description": "List available things",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            }
        ]
        user_code = (
            "import mcp\n"
            "import mcp.servers.demo_server as demo\n"
            "result = await demo.list_things()\n"
            "assert result == ['ok']\n"
            "assert 'demo-server' in mcp_servers\n"
            "assert 'demo_server' in mcp.servers.__all__\n"
        )

        entrypoint = RootlessContainerSandbox._render_entrypoint(  # type: ignore[arg-type]
            None,
            user_code,
            metadata,
            ["demo-server"],
        )

        calls: List[Dict[str, object]] = []
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)
        writer = os.fdopen(write_fd, "wb", buffering=0)
        stdin_wrapper = io.TextIOWrapper(reader, encoding="utf-8")

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        original___stdout__ = sys.__stdout__
        original_stdin = sys.stdin

        def _send_response(message_id: int, payload: Dict[str, object]) -> None:
            response = {
                "type": "rpc_response",
                "id": message_id,
                "success": payload.get("success", True),
                "payload": payload,
            }
            if not response["success"]:
                response["error"] = payload.get("error", "RPC error")
            writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            writer.flush()

        outer_self = self

        class _StdoutCapture:
            def __init__(self) -> None:
                self._buffer = ""

            def write(self, data: str) -> None:
                self._buffer += data
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    if not line:
                        continue
                    message = json.loads(line)
                    calls.append(message)
                    msg_type = message.get("type")
                    if msg_type == "stdout":
                        stdout_chunks.append(str(message.get("data", "")))
                    elif msg_type == "stderr":
                        stderr_chunks.append(str(message.get("data", "")))
                    elif msg_type == "rpc_request":
                        payload = message.get("payload", {})
                        req_type = payload.get("type")
                        message_id = message.get("id")
                        if req_type == "call_tool":
                            outer_self.assertEqual(payload.get("server"), "demo-server")
                            outer_self.assertEqual(payload.get("tool"), "list_things")
                            _send_response(message_id, {"success": True, "result": ["ok"]})
                        elif req_type == "list_tools":
                            outer_self.assertEqual(payload.get("server"), "demo-server")
                            _send_response(message_id, {"success": True, "tools": metadata[0]["tools"]})
                        else:
                            raise AssertionError(f"Unexpected RPC payload: {payload}")
                    else:
                        raise AssertionError(f"Unexpected message type: {message}")

            def flush(self) -> None:  # pragma: no cover - compatibility shim
                return None

        fake_stdout = _StdoutCapture()

        namespace: dict[str, object] = {"__name__": "__main__"}
        original_modules = {name for name in sys.modules if name.startswith("mcp")}
        sandbox_exports: dict[str, object] | None = None
        mcp_package = None
        runtime_module = None
        demo_module = None

        try:
            sys.__stdout__ = fake_stdout  # type: ignore[assignment]
            sys.stdin = stdin_wrapper
            exec(entrypoint, namespace)
            sandbox_exports = namespace.get("mcp_servers")  # capture before cleanup
            mcp_package = namespace.get("mcp")
            demo_module = sys.modules.get("mcp.servers.demo_server")
            runtime_module = sys.modules.get("mcp.runtime")
        finally:
            sys.__stdout__ = original___stdout__  # type: ignore[assignment]
            sys.stdin = original_stdin
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            writer.close()
            stdin_wrapper.close()
            for name in list(sys.modules):
                if name.startswith("mcp") and name not in original_modules:
                    sys.modules.pop(name, None)

        self.assertTrue(any(call.get("type") == "rpc_request" for call in calls))
        self.assertEqual("".join(stdout_chunks), "")
        self.assertEqual("".join(stderr_chunks), "")
        self.assertIsInstance(sandbox_exports, dict)
        self.assertIn("demo-server", sandbox_exports)
        self.assertIsNotNone(demo_module)
        self.assertTrue(hasattr(demo_module, "list_things"))
        self.assertIsNotNone(runtime_module)
        self.assertIsNotNone(mcp_package)
        if runtime_module is not None:
            self.assertEqual(runtime_module.discovered_servers(), ("demo-server",))
            self.assertTrue(hasattr(runtime_module, "query_tool_docs"))
            self.assertTrue(hasattr(runtime_module, "search_tool_docs"))
        if runtime_module is not None and mcp_package is not None:
            self.assertIs(getattr(mcp_package, "runtime", None), runtime_module)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
