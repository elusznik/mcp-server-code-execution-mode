# MCP Server Code Execution Mode

An MCP server that executes Python code in isolated rootless containers with optional MCP server proxying.

[![Anthropic Engineering](https://img.shields.io/badge/Anthropic-Engineering-orange)](https://www.anthropic.com/engineering/code-execution-with-mcp)
[![Cloudflare Blog](https://img.shields.io/badge/Cloudflare-Code_Mode-orange)](https://blog.cloudflare.com/code-mode/)
[![MCP Protocol](https://img.shields.io/badge/MCP-Documentation-green)](https://modelcontextprotocol.io/)

## Overview

This bridge implements the **"Code Execution with MCP"** pattern—a revolutionary approach to using Model Context Protocol tools. Instead of exposing all MCP tools directly to Claude (consuming massive context), the bridge:

1. **Auto-discovers** configured MCP servers
2. **Proxies tools** into sandboxed code execution
3. **Eliminates context overhead** (95%+ reduction)
4. **Enables complex workflows** through Python code

## Key Features

### 🔒 Security First
- **Rootless containers** - No privileged helpers required
- **Network isolation** - No network access
- **Read-only filesystem** - Immutable root
- **Dropped capabilities** - No system access
- **Unprivileged user** - Runs as UID 65534
- **Resource limits** - Memory, PIDs, CPU, time
- **Auto-cleanup** - Temporary IPC directories

### ⚡ Performance
- **Persistent clients** - MCP servers stay warm
- **Context efficiency** - 95%+ reduction vs traditional MCP
- **Async execution** - Proper resource management
- **Single tool** - Only `run_python` in Claude's context

### 🔧 Developer Experience
- **Multiple access patterns**:
  ```python
  mcp_servers["server"]           # Dynamic lookup
  mcp_server_name                 # Attribute access
  from mcp.servers.server import * # Module import
  ```
- **Top-level await** - Modern Python patterns
- **Type-safe** - Proper signatures and docs
- **Compact responses** - Plain-text output by default with optional TOON blocks when requested

### Response Formats
- **Default (compact)** – responses render as plain text plus a minimal `structuredContent` payload containing only non-empty fields. `stdout`/`stderr` lines stay intact, so prompts remain lean without sacrificing content.
- **Optional TOON** – set `MCP_BRIDGE_OUTPUT_MODE=toon` to emit [Token-Oriented Object Notation](https://github.com/toon-format/toon) blocks. We still drop empty fields and mirror the same structure in `structuredContent`; TOON is handy when you want deterministic tokenisation for downstream prompts.
- **Fallback JSON** – if the TOON encoder is unavailable we automatically fall back to pretty JSON blocks while preserving the trimmed payload.

## Quick Start

### 1. Prerequisites (macOS or Linux)

- Install a rootless container runtime (Podman or Docker).
  - macOS: `brew install podman` or `brew install --cask docker`
  - Ubuntu/Debian: `sudo apt-get install -y podman` or `curl -fsSL https://get.docker.com | sh`
- Install [uv](https://docs.astral.sh/uv/) to manage this project:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Pull a Python base image once your runtime is ready:
  ```bash
  podman pull python:3.12-slim
  # or
  docker pull python:3.12-slim
  ```

### 2. Install Dependencies

Use uv to sync the project environment:

```bash
uv sync
```

### 3. Launch Bridge

```bash
uvx --from git+https://github.com/elusznik/mcp-server-code-execution-mode mcp-server-code-execution-mode run
```

If you prefer to run from a local checkout, the equivalent command is:

```bash
uv run python mcp_server_code_execution_mode.py
```

### 4. Register with Claude Code

**File**: `~/.config/mcp/servers/mcp-server-code-execution-mode.json`

```json
{
  "mcpServers": {
    "mcp-server-code-execution-mode": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/elusznik/mcp-server-code-execution-mode",
        "mcp-server-code-execution-mode",
        "run"
      ],
      "env": {
        "MCP_BRIDGE_RUNTIME": "podman"
      }
    }
  }
}
```

**Restart Claude Code**

### 5. Execute Code

```python
# Use MCP tools in sandboxed code
result = await mcp_filesystem.read_file(path='/tmp/test.txt')

# Complex workflows
data = await mcp_search.search(query="TODO")
await mcp_github.create_issue(repo='owner/repo', title=data.title)
```

## Architecture

```
┌─────────────┐
│ MCP Client  │ (Claude Code)
└──────┬──────┘
       │ stdio
       ▼
┌──────────────┐
│ MCP Code Exec │ ← Discovers, proxies, manages
│ Bridge        │
└──────┬──────┘
       │ container
       ▼
┌─────────────┐
│ Container   │ ← Executes with strict isolation
│ Sandbox     │
└─────────────┘
```

**Process:**
1. Client calls `run_python(code, servers, timeout)`
2. Bridge loads requested MCP servers
3. Prepares a sandbox invocation: collects MCP tool metadata, writes an entrypoint into a shared `/ipc` volume, and exports `MCP_AVAILABLE_SERVERS`
4. Generated entrypoint rewires stdio into JSON-framed messages and proxies MCP calls over the container's stdin/stdout pipe
5. Runs container with security constraints
6. Host stream handler processes JSON frames, forwards MCP traffic, enforces timeouts, and cleans up

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_BRIDGE_RUNTIME` | auto | Container runtime (podman/docker) |
| `MCP_BRIDGE_IMAGE` | python:3.12-slim | Container image |
| `MCP_BRIDGE_TIMEOUT` | 30s | Default timeout |
| `MCP_BRIDGE_MAX_TIMEOUT` | 120s | Max timeout |
| `MCP_BRIDGE_MEMORY` | 512m | Memory limit |
| `MCP_BRIDGE_PIDS` | 128 | Process limit |
| `MCP_BRIDGE_CPUS` | - | CPU limit |
| `MCP_BRIDGE_CONTAINER_USER` | 65534:65534 | Run as UID:GID |
| `MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT` | 300s | Shutdown delay |
| `MCP_BRIDGE_STATE_DIR` | `./.mcp-bridge` | Host directory for IPC sockets and temp state |
| `MCP_BRIDGE_OUTPUT_MODE` | `compact` | Response text format (`compact` or `toon`) |
| `MCP_BRIDGE_LOG_LEVEL` | `INFO` | Bridge logging verbosity |

### Server Discovery

**Scanned Locations:**
- `~/.claude.json`
- `~/Library/Application Support/Claude Code/claude_code_config.json`
- `~/Library/Application Support/Claude/claude_code_config.json` *(early Claude Code builds)*
- `~/Library/Application Support/Claude/claude_desktop_config.json` *(Claude Desktop fallback)*
- `~/.config/mcp/servers/*.json`
- `./claude_code_config.json`
- `./claude_desktop_config.json` *(project-local fallback)*
- `./mcp-servers/*.json`

**Example Server** (`~/.config/mcp/servers/filesystem.json`):
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    }
  }
}
```

### Docker MCP Gateway Integration

When you rely on `docker mcp gateway run` to expose third-party MCP servers, the bridge simply executes the gateway binary. The gateway is responsible for pulling tool images and wiring stdio transports, so make sure the host environment is ready:

- Run `docker login` for every registry referenced in the gateway catalog (e.g. Docker Hub `mcp/*` images, `ghcr.io/github/github-mcp-server`). Without cached credentials the pull step fails before any tools come online.
- Provide required secrets for those servers—`github-official` needs `github.personal_access_token`, others may expect API keys or auth tokens. Use `docker mcp secret set <name>` (or whichever mechanism your gateway is configured with) so the container sees the values at start-up.
- Mirror any volume mounts or environment variables that the catalog expects (filesystem paths, storage volumes, etc.). Missing mounts or credentials commonly surface as `failed to connect: calling "initialize": EOF` during the stdio handshake.
- If `list_tools` only returns the internal management helpers (`mcp-add`, `code-mode`, …), the gateway never finished initializing the external servers—check the gateway logs for missing secrets or registry access errors.

### State Directory & Volume Sharing

- Runtime artifacts (including the generated `/ipc/entrypoint.py` and related handshake metadata) live under `./.mcp-bridge/` by default. Set `MCP_BRIDGE_STATE_DIR` to relocate them.
- When the selected runtime is Podman, the bridge automatically issues `podman machine set --rootful --now --volume <state_dir>:<state_dir>` so the VM can mount the directory.
- Docker Desktop does not expose a CLI for file sharing; ensure the chosen state directory is marked as shared in Docker Desktop → Settings → Resources → File Sharing before running the bridge.
- To verify a share manually, run `docker run --rm -v $PWD/.mcp-bridge:/ipc alpine ls /ipc` (or the Podman equivalent) and confirm the files are visible.

## Usage Examples

### File Processing

```python
# List and filter files
files = await mcp_filesystem.list_directory(path='/tmp')

for file in files:
    content = await mcp_filesystem.read_file(path=file)
    if 'TODO' in content:
        print(f"TODO in {file}")
```

### Data Pipeline

```python
# Extract data
transcript = await mcp_google_drive.get_document(documentId='abc123')

# Process
summary = transcript[:500] + "..."

# Store
await mcp_salesforce.update_record(
    objectType='SalesMeeting',
    recordId='00Q5f000001abcXYZ',
    data={'Notes': summary}
)
```

### Multi-System Workflow

```python
# Jira → GitHub migration
issues = await mcp_jira.search_issues(project='API', status='Open')

for issue in issues:
    details = await mcp_jira.get_issue(id=issue.id)

    if 'bug' in details.description.lower():
        await mcp_github.create_issue(
            repo='owner/repo',
            title=f"Bug: {issue.title}",
            body=details.description
        )
```

### Inspect Available Servers

```python
from mcp import runtime

print("Discovered:", runtime.discovered_servers())
print("Loaded metadata:", runtime.list_loaded_server_metadata())
print("Selectable via RPC:", await runtime.list_servers())

# Peek at tool docs for a server that's already loaded in this run
loaded = runtime.list_loaded_server_metadata()
if loaded:
  first = runtime.describe_server(loaded[0]["name"])
  for tool in first["tools"]:
    print(tool["alias"], "→", tool.get("description", ""))

# Ask for summaries or full schemas only when needed
if loaded:
  summaries = await runtime.query_tool_docs(loaded[0]["name"])
  detailed = await runtime.query_tool_docs(
    loaded[0]["name"],
    tool=summaries[0]["toolAlias"],
    detail="full",
  )
  print("Summaries:", summaries)
  print("Detailed doc:", detailed)

# Fuzzy search across loaded servers without rehydrating every schema
results = await runtime.search_tool_docs("calendar events", limit=3)
for result in results:
  print(result["server"], result["tool"], result.get("description", ""))
```

Example output seen by the LLM when running the snippet above with the stub server:

```
Discovered: ('stub',)
Loaded metadata: ({'name': 'stub', 'alias': 'stub', 'tools': [{'name': 'echo', 'alias': 'echo', 'description': 'Echo the provided message', 'input_schema': {...}}]},)
Selectable via RPC: ('stub',)
```

## Security

### Container Constraints

| Constraint | Setting | Purpose |
|------------|---------|---------|
| Network | `--network none` | No external access |
| Filesystem | `--read-only` | Immutable base |
| Capabilities | `--cap-drop ALL` | No system access |
| Privileges | `no-new-privileges` | No escalation |
| User | `65534:65534` | Unprivileged |
| Memory | `--memory 512m` | Resource cap |
| PIDs | `--pids-limit 128` | Process cap |
| Workspace | tmpfs, noexec | Safe temp storage |

### Capabilities Matrix

| Action | Allowed | Details |
|--------|---------|---------|
| Import stdlib | ✅ | Python standard library |
| Access MCP tools | ✅ | Via proxies |
| Memory ops | ✅ | Process data |
| Write to disk | ✅ | Only /tmp, /workspace |
| Network | ❌ | Completely blocked |
| Host access | ❌ | No system calls |
| Privilege escalation | ❌ | Prevented by sandbox |
| Container escape | ❌ | Rootless + isolation |

## Documentation

- **README.md** - This file, quick start
- **[GUIDE.md](GUIDE.md)** - Comprehensive user guide
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Technical deep dive
- **[HISTORY.md](HISTORY.md)** - Evolution and lessons
- **[STATUS.md](STATUS.md)** - Current state and roadmap

## Resources

### External
- [Code Execution with MCP (Anthropic)](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Code Mode (Cloudflare)](https://blog.cloudflare.com/code-mode/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Dynamic MCPs with Docker](https://www.docker.com/blog/dynamic-mcps-stop-hardcoding-your-agents-world/)

## Status

### ✅ Implemented
- Rootless container sandbox
- Single `run_python` tool
- MCP server proxying
- Persistent clients
- Comprehensive docs

### 🔄 In Progress
- Automated testing
- Observability (logging, metrics)
- Policy controls
- Runtime diagnostics

### 📋 Roadmap
- Connection pooling
- Web UI
- Multi-language support
- Workflow orchestration
- Agent-visible discovery channel (host-proxied `mcp-find`/`mcp-add`)
- Execution telemetry (structured logs, metrics, traces)
- Persistent and shareable code-mode artifacts

## License

GPLv3 License

## Support

For issues or questions, see the documentation or file an issue.
