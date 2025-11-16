# MCP Server Code Execution Mode Architecture

## Overview

The bridge runs user-supplied Python inside a **rootless container** and proxies
host-managed MCP servers into that sandbox when requested. Historical prototypes
with significant security flaws were abandoned rather than archived, serving as
lessons for what not to do in secure code execution.

## High-Level Flow

```
┌───────────────────────────┐
│  MCP Client (e.g. Claude) │
└──────────────┬────────────┘
               │ JSON-RPC over stdio
               ▼
┌───────────────────────────┐
│ main.py / mcp_server_code_execution_mode │
│ - Exposes run_python tool │
│ - Validates input         │
│ - Loads MCP servers       │
└──────────────┬────────────┘
               │ Async subprocess + JSON stdio bridge
               ▼
┌───────────────────────────┐
│ Rootless container runtime│
│ (podman or docker rootless)│
│ - Read-only rootfs        │
│ - Tmpfs work dir          │
│ - No network              │
│ - No Linux capabilities   │
└──────────────┬────────────┘
               │
               ▼
┌───────────────────────────┐
│ python:3.14-slim image    │
│ - Runs async entrypoint   │
│ - Executes provided code  │
│ - Calls mcp_<alias> tools │
└───────────────────────────┘
```

Each execution request receives a fresh container and a temporary `/ipc`
mount that carries the generated sandbox entrypoint; JSON-framed messages over
stdio mediate MCP tool access through the host.

## Components

- **`main.py`**: Thin entry point that launches `mcp_server_code_execution_mode.main()` so the
  module can be registered as an MCP server.
- **`mcp_server_code_execution_mode.py`**: Implements the MCP server, persistent MCP client
  pool, JSON RPC handlers, request validation, and container lifecycle management.
- **`PersistentMCPClient`**: Keeps stdio sessions to discovered MCP servers
  alive across invocations, avoiding repeated cold starts and shutting down
  cleanly when the bridge stops.
- **`SandboxInvocation`**: Builds per-execution metadata, writes the generated
  entrypoint into `/ipc`, injects `MCP_AVAILABLE_SERVERS`, and services
  JSON-RPC requests from the sandbox via `handle_rpc`.
- **Runtime helpers**: The generated entrypoint exposes `mcp.runtime` helpers
  (`discovered_servers`, `list_servers`, `list_servers_sync`, `list_tools`,
  `list_tools_sync`, `query_tool_docs`, `query_tool_docs_sync`,
  `search_tool_docs`, `search_tool_docs_sync`, `capability_summary`,
  `describe_server`, `list_loaded_server_metadata`) so sandboxed code can
  enumerate options before loading additional tools and answer high-level
  capability questions without exploratory code.
- **Container runtime**: Either `podman` or rootless `docker` must be available
  in the user namespace. Runtime discovery prefers the value from the
  `MCP_BRIDGE_RUNTIME` environment variable.
- **Podman machine management**: When using Podman, the bridge automatically
  starts the Podman machine if not running and shuts it down after
  `MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT` seconds of inactivity (default 300s/5min).
- **Historical context**: The project evolved through failed security experiments
  to the current robust architecture; see `HISTORY.md` for the evolution story.

## Request Lifecycle

1. MCP client invokes the `run_python` tool with code, optional timeout, and an
   optional list of MCP server names.
2. The bridge discovers server definitions (Claude Code config files and
  `~/.config/mcp/servers/*.json`, with legacy Claude Desktop paths as
  fallbacks) and boots persistent MCP clients for the requested servers.
3. `SandboxInvocation` creates a temporary `/ipc` directory, writes the
  generated `entrypoint.py`, ensures the runtime can mount it, and exports
  `MCP_AVAILABLE_SERVERS` with serialized tool metadata.
4. The container launches `python -u /ipc/entrypoint.py`; the entrypoint rewires
  stdio into JSON frames, installs `mcp_servers`/`mcp_<alias>` proxies, and uses
  an asyncio reader on stdin to receive host RPC responses while supporting
  top-level `await`.
5. The container executes the user code with network disabled, read-only
   filesystem, tmpfs workspace, dropped capabilities, and user `65534:65534`.
   Memory, PID, and CPU limits are derived from environment variables, and
   `--no-new-privileges` is enforced.
6. The bridge consumes JSON frames from stdout/stderr, routes MCP requests via
  `SandboxInvocation.handle_rpc`, enforces the timeout, and returns a
  `CallToolResult` where `structuredContent` carries the full status/IO payload
  (with empty strings/collections dropped). `content[0].text` is rendered as
  compact plain text by default, or as a TOON block when `MCP_BRIDGE_OUTPUT_MODE=toon`.

  The sandbox helper summary embedded in the tool schema only advertises these
  discovery functions; it never serialises individual server or tool metadata.
  As a result, the initial system prompt is effectively constant in size and the
  agent fetches detailed documentation on demand via `discovered_servers()`,
  `query_tool_docs()`, or `search_tool_docs()`.
7. Temporary IPC assets are cleaned up and MCP clients remain warm for future
   calls.

## Configuration

Environment variables allow most execution parameters to be tuned without code
changes:

| Variable | Purpose | Default |
|----------|---------|---------|
| `MCP_BRIDGE_RUNTIME` | Force container runtime (`podman` or `docker`) | auto-detect |
| `MCP_BRIDGE_IMAGE` | Container image to run | `python:3.14-slim` |
| `MCP_BRIDGE_TIMEOUT` | Default timeout (seconds) | 30 |
| `MCP_BRIDGE_MAX_TIMEOUT` | Hard timeout ceiling | 120 |
| `MCP_BRIDGE_MEMORY` | Memory limit passed to `--memory` | 512m |
| `MCP_BRIDGE_PIDS` | PID limit for `--pids-limit` | 128 |
| `MCP_BRIDGE_CPUS` | CPU quota for `--cpus` | host default |
| `MCP_BRIDGE_CONTAINER_USER` | UID:GID inside container | 65534:65534 |
| `MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT` | Auto-shutdown delay for Podman machine (seconds) | 300 |

## Security Posture

- **Rootless execution**: The container runtime operates entirely within the
  invoking user namespace; no privileged helpers are required.
- **Ephemeral filesystem**: Only tmpfs mounts are writable, preventing the code
  from persisting data on the host.
- **Capability drop**: With no capabilities and no-new-privileges set, the
  process cannot escalate privileges inside the container.
- **Network disabled**: Requests cannot reach external services or internal
  hosts.
- **Resource limits**: Memory, PID, and CPU caps prevent abusive resource use.
- **MCP mediation**: All MCP traffic traverses the host RPC server, keeping
  audit visibility and allowing future policy enforcement.

## Current Limitations

- Automated end-to-end tests for the container layer are not bundled because
  they would require a runtime and image download during test runs.
- Observability is limited to basic logging; structured events and metrics
  remain future work.
- Runtime discovery stops at the first available binary; richer diagnostics and
  configurability are desired.

## Extensibility Notes

- Additional policy checks (e.g. allowlists/denylists) can be added inside
  `SandboxRPCServer._dispatch` before forwarding MCP calls.
- Alternative images can be supplied through `MCP_BRIDGE_IMAGE` provided they
  contain Python 3 and accept `python -` to execute code from stdin.
- Additional hardening (e.g. seccomp profiles or AppArmor) can be layered by
  injecting runtime-specific arguments once the chosen container runtime is
  known.

