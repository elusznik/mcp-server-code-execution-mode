import os
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
        with patch.dict("os.environ", {"MCP_BRIDGE_OUTPUT_MODE": "toon"}, clear=False):
            with patch.object(bridge_module.bridge, "execute_code", async_mock):
                response = await bridge_module.call_tool(
                    "run_python",
                    {"code": "print('ok')"},
                )

        self.assertFalse(response.isError)
        content = response.content[0]
        self.assertEqual(content.type, "text")
        body = _extract_toon_body(content.text)
        decoded = toon_decode(body)
        expected = {
            "status": "success",
            "summary": "Success",
            "exitCode": 0,
            "stdout": ["line1", "line2"],
        }
        self.assertEqual(decoded, expected)
        self.assertEqual(response.structuredContent, expected)
        self.assertNotIn("stderr", decoded)

    async def test_timeout_response_includes_error_details(self) -> None:
        if toon_decode is None:
            self.skipTest("toon-format not installed")
        timeout_exc = SandboxTimeout(
            "Execution timed out after 5 seconds",
            stdout="partial output",
            stderr="traceback info",
        )

        async_mock = AsyncMock(side_effect=timeout_exc)
        with patch.dict("os.environ", {"MCP_BRIDGE_OUTPUT_MODE": "toon"}, clear=False):
            with patch.object(bridge_module.bridge, "execute_code", async_mock):
                response = await bridge_module.call_tool(
                    "run_python",
                    {"code": "print('slow')", "timeout": 5},
                )

        self.assertTrue(response.isError)
        content = response.content[0]
        self.assertEqual(content.type, "text")
        body = _extract_toon_body(content.text)
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
        self.assertEqual(
            response.structuredContent,
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
        with patch.dict("os.environ", {"MCP_BRIDGE_OUTPUT_MODE": "toon"}, clear=False):
            response = await bridge_module.call_tool("run_python", {})
        self.assertTrue(response.isError)
        content = response.content[0]
        self.assertEqual(content.type, "text")
        body = _extract_toon_body(content.text)
        decoded = toon_decode(body)
        expected = {
            "status": "validation_error",
            "summary": "Missing 'code' argument",
            "error": "Missing 'code' argument",
        }
        self.assertEqual(decoded, expected)
        self.assertEqual(response.structuredContent, expected)

    async def test_success_response_skips_empty_streams(self) -> None:
        if toon_decode is None:
            self.skipTest("toon-format not installed")
        sample_result = SandboxResult(True, 0, "", "")

        async_mock = AsyncMock(return_value=sample_result)
        with patch.dict("os.environ", {"MCP_BRIDGE_OUTPUT_MODE": "toon"}, clear=False):
            with patch.object(bridge_module.bridge, "execute_code", async_mock):
                response = await bridge_module.call_tool(
                    "run_python",
                    {"code": "print('nothing to see')"},
                )

        self.assertFalse(response.isError)
        content = response.content[0]
        body = _extract_toon_body(content.text)
        decoded = toon_decode(body)
        self.assertNotIn("stdout", decoded)
        self.assertNotIn("stderr", decoded)
        self.assertNotIn("stdout", response.structuredContent)
        self.assertNotIn("stderr", response.structuredContent)
        expected = {
            "status": "success",
            "summary": "Success (no output)",
            "exitCode": 0,
        }
        self.assertEqual(decoded, expected)
        self.assertEqual(response.structuredContent, expected)

    def test_empty_error_field_is_omitted(self) -> None:
        response = bridge_module._build_tool_response(  # type: ignore[attr-defined]
            status="error",
            summary="Example",
            error="",
        )
        self.assertTrue(response.isError)
        self.assertNotIn("error", response.structuredContent)

    async def test_default_output_mode_renders_plain_text(self) -> None:
        sample_result = SandboxResult(True, 0, "alpha\nbeta\n", "")

        async_mock = AsyncMock(return_value=sample_result)
        with patch.object(bridge_module.bridge, "execute_code", async_mock):
            response = await bridge_module.call_tool(
                "run_python",
                {"code": "print('alpha');print('beta')"},
            )

        self.assertFalse(response.isError)
        self.assertEqual(response.content[0].type, "text")
        self.assertEqual(response.content[0].text.strip(), "alpha\nbeta")
        self.assertNotIn("```", response.content[0].text)
        self.assertEqual(response.structuredContent, {"stdout": ["alpha", "beta"]})


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    unittest.main()
