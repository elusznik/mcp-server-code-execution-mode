import re
import unittest
from unittest.mock import AsyncMock, patch

try:  # pragma: no cover - runtime import with graceful fallback
    from toon_format import decode as toon_decode  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - dependency missing during static analysis
    toon_decode = None  # type: ignore[assignment]

import mcp_server_code_execution_mode as bridge_module
from mcp_server_code_execution_mode import SandboxResult, SandboxTimeout


def _extract_toon_body(text: str) -> str:
    match = re.search(r"```toon\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise AssertionError(f"No TOON block found in: {text!r}")
    return match.group(1).strip()


class ToonResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_response_uses_toon_block(self) -> None:
        if toon_decode is None:
            self.skipTest("toon-format not installed")
        sample_result = SandboxResult(True, 0, "line1\nline2\n", "")

        async_mock = AsyncMock(return_value=sample_result)
        with patch.object(bridge_module.bridge, "execute_code", async_mock):
            response = await bridge_module.call_tool(
                "run_python",
                {"code": "print('ok')"},
            )

        self.assertEqual(len(response), 1)
        content = response[0]["content"][0]
        self.assertEqual(content["type"], "text")
        body = _extract_toon_body(content["text"])
        decoded = toon_decode(body)
        self.assertEqual(
            decoded,
            {
                "status": "success",
                "summary": "Success",
                "exitCode": 0,
                "stdout": ["line1", "line2"],
                "stderr": [],
            },
        )

    async def test_timeout_response_includes_error_details(self) -> None:
        if toon_decode is None:
            self.skipTest("toon-format not installed")
        timeout_exc = SandboxTimeout(
            "Execution timed out after 5 seconds",
            stdout="partial output",
            stderr="traceback info",
        )

        async_mock = AsyncMock(side_effect=timeout_exc)
        with patch.object(bridge_module.bridge, "execute_code", async_mock):
            response = await bridge_module.call_tool(
                "run_python",
                {"code": "print('slow')", "timeout": 5},
            )

        content = response[0]["content"][0]
        body = _extract_toon_body(content["text"])
        decoded = toon_decode(body)
        self.assertEqual(
            decoded,
            {
                "status": "timeout",
                "summary": "Timeout: execution exceeded 5s",
                "stdout": ["partial output"],
                "stderr": ["traceback info"],
                "error": "Execution timed out after 5 seconds",
                "timeoutSeconds": 5,
            },
        )

    async def test_validation_error_uses_toon(self) -> None:
        if toon_decode is None:
            self.skipTest("toon-format not installed")
        response = await bridge_module.call_tool("run_python", {})
        content = response[0]["content"][0]
        body = _extract_toon_body(content["text"])
        decoded = toon_decode(body)
        self.assertEqual(
            decoded,
            {
                "status": "validation_error",
                "summary": "Missing 'code' argument",
                "stdout": [],
                "stderr": [],
                "error": "Missing 'code' argument",
            },
        )


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    unittest.main()
