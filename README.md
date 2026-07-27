# MCP Code Execution Server: Zero-Context Discovery for 100+ MCP Tools

[![MCP Toplist](https://mcptoplist.com/badge/glama%2Felusznik%2Fmcp-server-code-execution-mode.svg)](https://mcptoplist.com/server/glama%2Felusznik%2Fmcp-server-code-execution-mode)

[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/elusznik-mcp-server-code-execution-mode-badge.png)](https://mseep.ai/app/elusznik-mcp-server-code-execution-mode)

**Stop paying 30,000 tokens per query.** This bridge implements Anthropic's discovery pattern with rootless security—reducing MCP context from 30K to 200 tokens while proxying any stdio server.

[![Anthropic Engineering](https://img.shields.io/badge/Anthropic-Engineering-orange)](https://www.anthropic.com/engineering/code-execution-with-mcp)
[![Cloudflare Blog](https://img.shields.io/badge/Cloudflare-Code_Mode-orange)](https://blog.cloudflare.com/code-mode/)
[![Docker MCP Gateway](https://img.shields.io/badge/Docker-MCP_Gateway-blue)](https://www.docker.com/blog/dynamic-mcps-stop-hardcoding-your-agents-world/)
[![Apple Machine Learning](https://img.shields.io/badge/Apple_Machine_Learning-CodeAct?logo=apple&style=flat-square)](https://machinelearning.apple.com/research/codeact)
[![MCP Protocol](https://img.shields.io/badge/MCP-Documentation-green)](https://modelcontextprotocol.io/)
[![Verified on MseeP](https://mseep.ai/badge.svg)](https://mseep.ai/app/4a84c349-1795-41fc-a299-83d4a29feee8)

## Overview

This bridge implements the **"Code Execution with MCP"** pattern, a convergence of ideas from industry leaders:

- **Apple's [CodeAct](https://machinelearning.apple.com/research/codeact)**: "Your LLM Agent Acts Better when Generating Code."
- **Anthropic's [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)**: "Building more efficient agents."
- **Cloudflare's [Code Mode](https://blog.cloudflare.com/code-mode/)**: "LLMs are better at writing code to call MCP, than at calling MCP directly."
- **Docker's [Dynamic MCPs](https://www.docker.com/blog/dynamic-mcps-stop-hardcoding-your-agents-world/)**: "Stop Hardcoding Your Agents’ World."
- **[Terminal Bench](https://www.tbench.ai)'s [Terminus](https://www.tbench.ai/terminus)**: "A realistic terminal environment for evaluating LLM agents."

Instead of exposing hundreds of individual tools to the LLM (which consumes massive context and confuses the model), this bridge exposes **one** tool: `run_python`. The LLM writes Python code to discover, call, and compose other tools.

### Why This vs. JS "Code Mode"?

While there are JavaScript-based alternatives (like [`universal-tool-calling-protocol/code-mode`](https://github.com/universal-tool-calling-protocol/code-mode)), this project is built for **Data Science** and **Security**:

| Feature | This Project (Python) | JS Code Mode (Node.js) |
| :--- | :--- | :--- |
| **Native Language** | **Python** (The language of AI/ML) | TypeScript/JavaScript |
| **Data Science** | **Native** (`pandas`, `numpy`, `scikit-learn`) | Impossible / Hacky |
| **Isolation** | **Hard** (Podman/Docker Containers) | Soft (Node.js VM) |
| **Security** | **Enterprise** (Rootless, No Net, Read-Only) | Process-level |
| **Philosophy** | **Infrastructure** (Standalone Bridge) | Library (Embeddable) |

**Choose this if:** You want your agent to analyze data, generate charts, use scientific libraries, or if you require strict container-based isolation for running untrusted code.

## What This Solves (That Others Don't)

### The Pain: MCP Token Bankruptcy

Connect Claude to 11 MCP servers with ~100 tools = **30,000 tokens** of tool schemas loaded into every prompt. That's **$0.09 per query** before you ask a single question. Scale to 50 servers and your context window *breaks*.

### Why Existing "Solutions" Fail

- **Docker MCP Gateway**: Manages containers beautifully, but still streams **all tool schemas** into Claude's context. No token optimization.
- **Cloudflare Code Mode**: V8 isolates are fast, but you **can't proxy your existing MCP servers** (Serena, Wolfram, custom tools). Platform lock-in.
- **Academic Papers**: Describe Anthropic's discovery pattern, but provide **no hardened implementation**.
- **Proofs of Concept**: Skip security (no rootless), skip persistence (cold starts), skip proxying edge cases.

### The Fix: Discovery-First Architecture

- **Constant 200-token overhead** regardless of server count
- **Proxy any stdio MCP server** into rootless containers
- **Fuzzy search across servers** without preloading schemas
- **Production-hardened** with capability dropping and security isolation

### Architecture: How It Differs

```text
Traditional MCP (Context-Bound)
┌─────────────────────────────┐
│   LLM Context (30K tokens)  │
│  - serverA.tool1: {...}     │
│  - serverA.tool2: {...}     │
│  - serverB.tool1: {...}     │
│  - … (dozens more)          │
└─────────────────────────────┘
        ↓
  LLM picks tool
        ↓
   Tool executes

This Bridge (Discovery-First)
┌─────────────────────────────┐
│  LLM Context (≈200 tokens)  │
│  “Use discovered_servers(), │
│   query_tool_docs(),        │
│   search_tool_docs()”       │
└─────────────────────────────┘
        ↓
      LLM discovers servers
        ↓
      LLM hydrates schemas
        ↓
      LLM writes Python
        ↓
   Bridge proxies execution
```

Result: constant overhead. Whether you manage 10 or 1000 tools, the system prompt stays right-sized and schemas flow only when requested.

### Comparison At A Glance

| Capability | Docker MCP Gateway | Cloudflare Code Mode | Research Patterns | This Bridge |
|------------|--------------------|----------------------|-------------------|--------------|
| Solves token bloat | ❌ Manual preload | ❌ Fixed catalog | ❌ Theory only | ✅ Discovery runtime |
| Universal MCP proxying | ✅ Containers | ⚠️ Platform-specific | ❌ Not provided | ✅ Any stdio server |
| Rootless security | ⚠️ Optional | ✅ V8 isolate | ❌ Not addressed | ✅ Cap-dropped sandbox |
| Auto-discovery | ⚠️ Catalog-bound | ❌ N/A | ❌ Not implemented | ✅ 12+ config paths |
| Tool doc search | ❌ | ❌ | ⚠️ Conceptual | ✅ `search_tool_docs()` |
| Production hardening | ⚠️ Depends on you | ✅ Managed service | ❌ Prototype | ✅ Tested bridge |

### Vs. Dynamic Toolsets (Speakeasy)

Speakeasy's [Dynamic Toolsets](https://www.speakeasy.com/blog/how-we-reduced-token-usage-by-100x-dynamic-toolsets-v2) use a 3-step flow: `search_tools` → `describe_tools` → `execute_tool`. While this saves tokens, it forces the agent into a "chatty" loop:

1. **Search**: "Find tools for GitHub issues"
2. **Describe**: "Get schema for `create_issue`"
3. **Execute**: "Call `create_issue`"

**This Bridge (Code-First) collapses that loop:**

1. **Code**: "Import `mcp_github`, search for 'issues', and create one if missing."

The agent writes a **single Python script** that performs discovery, logic, and execution in one round-trip. It's faster, cheaper (fewer intermediate LLM calls), and handles complex logic (loops, retries) that a simple "execute" tool cannot.

### Vs. OneMCP (Gentoro)

[OneMCP](https://github.com/Gentoro-OneMCP/onemcp) provides a "Handbook" chat interface where you ask questions and it plans execution. This is great for simple queries but turns the execution into a **black box**.

**This Bridge** gives the agent **raw, sandboxed control**. The agent isn't asking a black box to "do it"; the agent *is* the programmer, writing the exact code to interact with the API. This allows for precise edge-case handling and complex data processing that a natural language planner might miss.

### Unique Features

1. **Two-stage discovery** – `discovered_servers()` reveals what exists; `query_tool_docs(name)` loads only the schemas you need.
2. **Fuzzy search across servers** – let the model find tools without memorising catalog names:

    ```python
    from mcp import runtime

    matches = await runtime.search_tool_docs("calendar events", limit=5)
    for hit in matches:
        print(hit["server"], hit["tool"], hit.get("description", ""))
    ```

3. **Zero-copy proxying** – every tool call stays within the sandbox, mirrored over stdio with strict timeouts.
4. **Rootless by default** – Podman/Docker containers run with `--cap-drop=ALL`, read-only root, no-new-privileges, and explicit memory/PID caps.
5. **Compact + TOON output** – minimal plain-text responses for most runs, with deterministic TOON blocks available via `MCP_BRIDGE_OUTPUT_MODE=toon`.

### Who This Helps

- Teams juggling double-digit MCP servers who cannot afford context bloat.
- Agents that orchestrate loops, retries, and conditionals rather than single tool invocations.
- Security-conscious operators who need rootless isolation for LLM-generated code.
- Practitioners who want to reuse existing MCP catalogs without hand-curating manifests.

### Philosophy: The "No-MCP" Approach

This server aligns with the philosophy that [you might not need MCP at all](https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/) for every little tool. Instead of building rigid MCP servers for simple tasks, you can use this server to give your agent **raw, sandboxed access to Bash and Python**.

- **Ad-Hoc Tools**: Need a script to scrape a site or parse a file? Just write it and run it. No need to deploy a new MCP server.
- **Composability**: Pipe outputs between commands, save intermediate results to files, and use standard Unix tools.
- **Safety**: Unlike giving an agent raw shell access to your machine, this server runs everything in a secure, rootless container. You get the power of "Bash/Code" without the risk.

## Key Features

### 🛡️ Robustness & Reliability

- **Lazy Runtime Detection**: Starts up instantly even if Podman/Docker isn't ready. Checks for runtime only when code execution is requested.
- **Self-Reference Prevention**: Automatically detects and skips configurations that would launch the bridge recursively.
- **Noise Filtering**: Ignores benign JSON parse errors (like blank lines) from chatty MCP clients.
- **Smart Volume Sharing**: Probes Podman VMs to ensure volume sharing works, even on older versions.

### 🔒 Security First

- **Rootless containers** - No privileged helpers required
- **Network isolation** - No network access
- **Read-only filesystem** - Immutable root
- **Dropped capabilities** - No system access
- **Unprivileged user** - Runs as UID 65534
- **Resource limits** - Memory, PIDs, CPU, time
- **Auto-cleanup** - Temporary IPC directories

### ⚡ Performance

- **Persistent sessions** - Variables and state retained across calls
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

### 🧠 Persistent Memory System

- **Cross-session persistence** - Memory data survives container restarts and sessions
- **JSON-based storage** - Flexible value types (strings, dicts, lists, etc.)
- **Metadata support** - Add tags and custom metadata to memory entries
- **Atomic updates** - Update memory values with custom functions
- **Discovery-friendly** - List all memories and check existence

```python
# Save context for future sessions
save_memory("project_context", {
    "goal": "Build REST API",
    "current_task": "Implement auth",
    "decisions": ["Use JWT tokens", "PostgreSQL backend"]
})

# Retrieve in a later session
context = load_memory("project_context")
print(f"Working on: {context['current_task']}")

# Update incrementally
update_memory("project_context", lambda ctx: {
    **ctx,
    "decisions": ctx["decisions"] + ["Add rate limiting"]
})

# List all saved memories
for mem in list_memories():
    print(f"{mem['key']}: created {mem['created_at']}")

# Check existence before loading
if memory_exists("user_preferences"):
    prefs = load_memory("user_preferences")
```

Memory files are stored in `/projects/memory/` inside the container, which maps to `~/MCPs/user_tools/memory/` on the host. This persists across sessions and container restarts.

### Discovery Workflow

- `SANDBOX_HELPERS_SUMMARY` in the tool schema only advertises the discovery helpers (`discovered_servers()`, `list_servers()`, `query_tool_docs()`, `search_tool_docs()`, etc.). It never includes individual server or tool documentation.
- On first use the LLM typically calls `discovered_servers()` (or `list_servers_sync()` for the cached list) to enumerate MCP servers, then `query_tool_docs(server)` / `query_tool_docs_sync(server)` or `search_tool_docs("keyword")` / `search_tool_docs_sync("keyword")` to fetch the relevant subset of documentation.
- Tool metadata is streamed on demand, keeping the system prompt at roughly 200 tokens regardless of how many servers or tools are installed.
- Once the LLM has the docs it needs, it writes Python that uses the generated `mcp_<alias>` proxies or `mcp.runtime` helpers to invoke tools.

**Need a short description without probing the helpers?** Call `runtime.capability_summary()` to print a one-paragraph overview suitable for replying to questions such as “what can the code-execution MCP do?”

## Quick Start

### 1. Prerequisites (macOS or Linux)

- Check version: `python3 --version` (Python 3.11+ required)
- If needed, install Python via package manager or [python.org](https://python.org)
- macOS: `brew install podman` or `brew install --cask docker`
- Ubuntu/Debian: `sudo apt-get install -y podman` or `curl -fsSL https://get.docker.com | sh`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
podman pull python:3.13-slim
# or
docker pull python:3.13-slim
```

Note on Pydantic compatibility:

- If you use Python 3.14+, ensure you have a modern Pydantic release installed (for example, `pydantic >= 2.12.0`). Some older Pydantic versions or environments that install a separate `typing` package from PyPI may raise errors such as:

```text
TypeError: _eval_type() got an unexpected keyword argument 'prefer_fwd_module'
```

If you see this error, run:

```bash
pip install -U pydantic
pip uninstall typing  # if present; the stdlib's typing should be used
```

And re-run the project setup (e.g. remove `.venv/` and `uv sync`).

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

### 4. Register with Your Agent

Add the following server configuration to your agent's MCP settings file (e.g., `mcp_config.json`, `claude_desktop_config.json`, etc.):

```json
{
  "mcpServers": {
    "mcp-server-code-execution-mode": {
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

### 5. Execute Code

```python
# Use MCP tools in sandboxed code
result = await mcp_filesystem.read_file(path='/tmp/test.txt')

# Complex workflows
data = await mcp_search.search(query="TODO")
await mcp_github.create_issue(repo='owner/repo', title=data.title)
```

### Load Servers Explicitly

`run_python` only loads the MCP servers you request. Pass them via the `servers` array when you invoke the tool so proxies such as `mcp_serena` or `mcp_filesystem` become available inside the sandbox:

```json
{
  "code": "print(await mcp_serena.search(query='latest AI papers'))",
  "servers": ["serena", "filesystem"]
}
```

If you omit the list the discovery helpers still enumerate everything, but any RPC call that targets an unloaded server returns `Server '<name>' is not available`.

Note: The `servers` array only controls which proxies are generated for a sandbox invocation. It does not set server configuration fields such as `cwd`. The `cwd` property is part of the host/server config and LLMs should call `runtime.describe_server(name)` or inspect `runtime.list_loaded_server_metadata()` to discover the configured `cwd` before assuming the server's working directory.

Note: server configurations can include an optional `cwd` property. If present the bridge will start the host MCP server process in that working directory; agents should check `runtime.describe_server(name)` to discover a server's configured `cwd` before making assumptions.

## Testing

Project environments support CPython 3.11+. Ensure your local environment uses a compatible Python version:

```bash
uv python pin 3.13
uv sync
```

Runtime dependencies stay lean; dev dependencies (pytest, etc.) are available via the `dev` extra:

```bash
# Install with dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Or run without installing dev dependencies permanently
uv run --with pytest pytest
```

## Architecture

```text
┌─────────────┐
│ MCP Client  │ (Your Agent)
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

### Zero-Context Discovery

Unlike traditional MCP servers that preload every tool definition (sometimes 30k+ tokens), this bridge pins its system prompt to roughly 200 tokens and trains the LLM to discover what it needs on demand:

1. LLM calls `discovered_servers()` → learns which bridges are available without loading schemas.
2. LLM calls `query_tool_docs("serena")` → hydrates just that server's tool docs, optionally filtered per tool.
3. LLM writes orchestration code → invokes helpers like `mcp_serena.search()` or `mcp.runtime.call_tool()`.

**Result:** context usage stays effectively constant no matter how many MCP servers you configure.

**Process:**

1. Client calls `run_python(code, servers, timeout)`
2. Bridge loads requested MCP servers
3. Prepares a sandbox invocation: collects MCP tool metadata, writes an entrypoint into a shared `/ipc` volume, and exports `MCP_AVAILABLE_SERVERS`
4. Generated entrypoint rewires stdio into JSON-framed messages and proxies MCP calls over the container's stdin/stdout pipe
5. **Persistent Execution**: The container is started once (if not running) and stays active.
6. **State Retention**: Variables, imports, and functions defined in one call are available in subsequent calls.
7. Host stream handler processes JSON frames, forwards MCP traffic, enforces timeouts, and keeps the container alive for the next request.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_BRIDGE_RUNTIME` | auto | Container runtime (podman/docker) |
| `MCP_BRIDGE_IMAGE` | python:3.14-slim | Container image |
| `MCP_BRIDGE_TIMEOUT` | 30s | Default timeout |
| `MCP_BRIDGE_MAX_TIMEOUT` | 120s | Max timeout |
| `MCP_BRIDGE_MEMORY` | 512m | Memory limit |
| `MCP_BRIDGE_PIDS` | 128 | Process limit |
| `MCP_BRIDGE_CPUS` | - | CPU limit |
| `MCP_BRIDGE_CONTAINER_USER` | 65534:65534 | Run as UID:GID |
| `MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT` | 300s | Shutdown delay |
| `MCP_BRIDGE_STATE_DIR` | `~/MCPs` | Host directory for IPC sockets and temp state |
| `MCP_BRIDGE_OUTPUT_MODE` | `compact` | Response text format (`compact` or `toon`) |
| `MCP_BRIDGE_LOG_LEVEL` | `INFO` | Bridge logging verbosity |

### Server Discovery

The bridge automatically discovers MCP servers from multiple configuration sources:

**Supported Locations:**

| Location | Name | Priority |
|----------|------|----------|
| `~/MCPs/` | User MCPs | Highest |
| `~/.config/mcp/servers/` | Standard MCP | |
| `./mcp-servers/` | Local Project | |
| `./.vscode/mcp.json` | VS Code Workspace | |
| `~/.claude.json` | Claude CLI | |
| `~/.cursor/mcp.json` | Cursor | |
| `~/.opencode.json` | OpenCode CLI | |
| `~/.codeium/windsurf/mcp_config.json` | Windsurf | |
| `~/Library/Application Support/Claude Code/claude_code_config.json` | Claude Code (macOS) | |
| `~/Library/Application Support/Claude/claude_desktop_config.json` | Claude Desktop (macOS) | |
| `~/Library/Application Support/Code/User/settings.json` | VS Code Global (macOS) | |
| `~/.config/Code/User/settings.json` | VS Code Global (Linux) | Lowest |

> **Note:** Earlier sources take precedence. If the same server is defined in multiple locations, the first one wins.

**Example Server** (`~/MCPs/filesystem.json`):

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

> **Note:** To prevent recursive launches, the bridge automatically skips any config entry that appears to start `mcp-server-code-execution-mode` again (including `uvx … mcp-server-code-execution-mode run`). Set `MCP_BRIDGE_ALLOW_SELF_SERVER=1` if you intentionally need to expose the bridge as a nested MCP server.

### Docker MCP Gateway Integration

When you rely on `docker mcp gateway run` to expose third-party MCP servers, the bridge simply executes the gateway binary. The gateway is responsible for pulling tool images and wiring stdio transports, so make sure the host environment is ready:

- Run `docker login` for every registry referenced in the gateway catalog (e.g. Docker Hub `mcp/*` images, `ghcr.io/github/github-mcp-server`). Without cached credentials the pull step fails before any tools come online.
- Provide required secrets for those servers—`github-official` needs `github.personal_access_token`, others may expect API keys or auth tokens. Use `docker mcp secret set <name>` (or whichever mechanism your gateway is configured with) so the container sees the values at start-up.
- Mirror any volume mounts or environment variables that the catalog expects (filesystem paths, storage volumes, etc.). Missing mounts or credentials commonly surface as `failed to connect: calling "initialize": EOF` during the stdio handshake.
- If `list_tools` only returns the internal management helpers (`mcp-add`, `code-mode`, …), the gateway never finished initializing the external servers—check the gateway logs for missing secrets or registry access errors.

### State Directory & Volume Sharing

- Runtime artifacts (including the generated `/ipc/entrypoint.py` and related handshake metadata) live under `~/MCPs/` by default. Set `MCP_BRIDGE_STATE_DIR` to relocate them.
- When the selected runtime is Podman, the bridge automatically issues `podman machine set --rootful --now --volume <state_dir>:<state_dir>` so the VM can mount the directory. On older `podman machine` builds that do not support `--volume`, the bridge now probes the VM with `podman machine ssh test -d <state_dir>` and proceeds if the share is already available.
- Docker Desktop does not expose a CLI for file sharing; ensure the chosen state directory is marked as shared in Docker Desktop → Settings → Resources → File Sharing before running the bridge.
- To verify a share manually, run `docker run --rm -v ~/MCPs:/ipc alpine ls /ipc` (or the Podman equivalent) and confirm the files are visible.

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
print("Cached servers:", runtime.list_servers_sync())
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
  print("Cached tools:", runtime.list_tools_sync(loaded[0]["name"]))
  print("Detailed doc:", detailed)

# Fuzzy search across loaded servers without rehydrating every schema
results = await runtime.search_tool_docs("calendar events", limit=3)
for result in results:
  print(result["server"], result["tool"], result.get("description", ""))

# Synchronous helpers for quick answers without extra awaits
print("Capability summary:", runtime.capability_summary())
print("Docs from cache:", runtime.query_tool_docs_sync(loaded[0]["name"]) if loaded else [])
print("Search from cache:", runtime.search_tool_docs_sync("calendar"))
```

Example output seen by the LLM when running the snippet above with the stub server:

```text
Discovered: ('stub',)
Loaded metadata: ({'name': 'stub', 'alias': 'stub', 'tools': [{'name': 'echo', 'alias': 'echo', 'description': 'Echo the provided message', 'input_schema': {...}}]},)
Selectable via RPC: ('stub',)
```

Clients that prefer `listMcpResources` can skip executing the helper snippet and instead request the
`resource://mcp-server-code-execution-mode/capabilities` resource. The server advertises it via
`resources/list`, and reading it returns the same helper summary plus a short checklist for loading
servers explicitly.

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
- [CodeAct: Your LLM Agent Acts Better when Generating Code (Apple)](https://machinelearning.apple.com/research/codeact)
- [Terminal Bench](https://github.com/laude-institute/terminal-bench)

## Status

### ✅ Implemented

- Rootless container sandbox
- Single `run_python` tool
- MCP server proxying
- Persistent sessions (state retention)
- Persistent clients (warm MCP servers)
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
