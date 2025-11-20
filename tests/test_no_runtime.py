from unittest.mock import patch

import pytest

from mcp_server_code_execution_mode import (
    RootlessContainerSandbox,
    SandboxError,
    detect_runtime,
)


def test_detect_runtime_none():
    with patch("shutil.which", return_value=None):
        assert detect_runtime() is None


@pytest.mark.asyncio
async def test_sandbox_init_no_runtime():
    with patch("shutil.which", return_value=None):
        sandbox = RootlessContainerSandbox()
        assert sandbox.runtime is None

        # Should not crash on init

        # Should crash on execute
        with pytest.raises(SandboxError, match="No container runtime found"):
            await sandbox.execute("print('hello')")

        # Should crash on _base_cmd
        with pytest.raises(SandboxError, match="No container runtime found"):
            sandbox._base_cmd()
