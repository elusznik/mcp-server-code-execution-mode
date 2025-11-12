# User Guide

A comprehensive guide to using the MCP Server Code Execution Mode bridge.

## Table of Contents

- [Installation](#installation)
- [Configuration](#configuration)
- [MCP Server Setup](#mcp-server-setup)
- [Usage Patterns](#usage-patterns)
- [Advanced Topics](#advanced-topics)
- [Troubleshooting](#troubleshooting)
- [Best Practices](#best-practices)

## Installation

### Prerequisites

#### 1. Container Runtime

**Option A: Podman (Recommended)**

```bash
# macOS
brew install podman

# Ubuntu/Debian
sudo apt-get install podman

# Verify
podman --version
```

**Option B: Rootless Docker**

```bash
# macOS
brew install docker

# Ubuntu/Debian
sudo apt-get install docker.io

# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

#### 2. Container Image

```bash
# Pull image
podman pull python:3.12-slim

# Or with Docker
docker pull python:3.12-slim

# Verify
podman images python:3.12-slim
```

### Setup

#### 1. Install Dependencies

```bash
# Using pip
pip install -r requirements.txt

# Or using uv (recommended)
uv sync
```

#### 2. Test Installation

```bash
uv run python mcp_server_code_execution_mode.py
```

This starts the MCP server. If no errors occur, the installation is successful.

#### 3. Register with MCP Client

**Claude Code:**

Create `~/.config/mcp/servers/mcp-server-code-execution-mode.json`:

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

**For other MCP clients:**

Add server to your client configuration:

```json
{
  "mcpServers": {
    "mcp-server-code-execution-mode": {
      "command": "python3",
      "args": ["/path/to/mcp_server_code_execution_mode.py"]
    }
  }
}
```

#### 4. Restart MCP Client

Restart Claude Code or your MCP client to load the new server.

## Configuration

### Environment Variables

Control bridge behavior with environment variables:

#### Runtime Configuration

```bash
# Force specific runtime
export MCP_BRIDGE_RUNTIME=podman
# or
export MCP_BRIDGE_RUNTIME=docker

# Custom container image
export MCP_BRIDGE_IMAGE=python:3.11-slim

# Default timeout (seconds)
export MCP_BRIDGE_TIMEOUT=30

# Maximum allowed timeout
export MCP_BRIDGE_MAX_TIMEOUT=120
```

#### Resource Limits

```bash
# Memory limit (format: number + unit)
export MCP_BRIDGE_MEMORY=512m
export MCP_BRIDGE_MEMORY=1g

# Process limit
export MCP_BRIDGE_PIDS=128

# CPU limit (can be decimal)
export MCP_BRIDGE_CPUS=2.0

# Container user (UID:GID)
export MCP_BRIDGE_CONTAINER_USER=1000:1000
```

#### Advanced Options

```bash
# Runtime idle timeout (seconds)
# Podman machine auto-shutdown delay
export MCP_BRIDGE_RUNTIME_IDLE_TIMEOUT=300
```

### Configuration File

**Note:** The bridge currently does not support loading variables from a `.env` file. All configuration must be done via environment variables or container runtime settings.

## MCP Server Setup

### Automatic Discovery

The bridge auto-discovers MCP servers from:

1. **Claude Code Config**
  - `~/.claude.json`
  - `~/Library/Application Support/Claude Code/claude_code_config.json`
  - `~/Library/Application Support/Claude/claude_code_config.json` *(early Claude Code builds)*
  - `~/Library/Application Support/Claude/claude_desktop_config.json` *(legacy Claude Desktop)*

2. **MCP Servers Directory**
   - `~/.config/mcp/servers/*.json`
   - `./mcp-servers/*.json`

3. **Local Config**
  - `./claude_code_config.json`
  - `./claude_desktop_config.json` *(legacy fallback)*

### Example: Filesystem Server

```bash
# Create server config
mkdir -p ~/.config/mcp/servers
cat > ~/.config/mcp/servers/filesystem.json << 'EOF'
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    }
  }
}
EOF
```

### Example: PostgreSQL Server

```bash
cat > ~/.config/mcp/servers/postgres.json << 'EOF'
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@localhost/mydb"],
      "env": {}
    }
  }
}
EOF
```

### Example: Git Server

```bash
cat > ~/.config/mcp/servers/git.json << 'EOF'
{
  "mcpServers": {
    "git": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-git", "/path/to/repo"],
      "env": {}
    }
  }
}
EOF
```

### Verifying Discovery

The bridge logs discovered servers on startup:

```
2024-01-01 12:00:00 - INFO - Loaded MCP servers: filesystem, postgres, git
```

## Usage Patterns

### Response Format (TOON)

The bridge wraps every tool response in a [Token-Oriented Object Notation](https://github.com/toon-format/toon) code block. TOON keeps the familiar JSON fields (status, stdout, stderr, etc.) but removes repeated keys and normalises arrays, typically saving 30–60% tokens for uniform tabular data. Lower token counts mean cheaper LLM calls and tighter prompts. Integrations can also read the same data from `CallToolResult.structuredContent`; the TOON block inside `CallToolResult.content[0].text` is simply a compact rendering. If the encoder is unavailable the bridge automatically falls back to pretty-printed JSON.

Before encoding we drop any empty strings or collections (stdout, stderr, error, servers, etc.) so both the structured payload and TOON block stay focused on meaningful content.

### Basic Pattern: Direct Tool Use

```python
# Call a single tool
result = await mcp_filesystem.read_file(path='/tmp/data.txt')
print(result)
```

### Pattern: Chained Operations

```python
# Chain multiple operations
data = await mcp_server.read_data()
processed = process(data)
await mcp_server.write_data(data=processed)
```

### Pattern: Data Pipeline

```python
# Extract
source_data = await mcp_source.fetch()

# Transform
cleaned = clean_data(source_data)

# Load
await mcp_destination.save(data=cleaned)

# Report
print(f"Processed {len(cleaned)} items")
```

### Pattern: Batch Processing

```python
# Get list
items = await mcp_api.list_items()

# Process in parallel
tasks = [
    mcp_api.process_item(id=item.id)
    for item in items
]

# Wait for all
results = await asyncio.gather(*tasks)
```

### Pattern: Error Handling

```python
try:
    result = await mcp_api.risky_operation()
except Exception as e:
    print(f"Operation failed: {e}")
    # Fallback or retry logic
```

### Pattern: Conditional Execution

```python
# Check before acting
status = await mcp_service.check_status()

if status.ready:
    await mcp_service.execute()
else:
    print("Service not ready")
```

### Pattern: Multi-Server Workflow

```python
# Get data from service A
data = await mcp_service_a.fetch_data(query='xyz')

# Process with service B
processed = await mcp_service_b.process(data=data)

# Save with service C
await mcp_service_c.save(data=processed)

# Notify with service D
await mcp_service_d.notify(message='Done')
```

### Pattern: Discover and Select Servers

```python
from mcp import runtime

# See everything the bridge knows about without loading schemas
print("Discovered:", runtime.discovered_servers())

# Metadata for servers already loaded in this run
print("Loaded metadata:", runtime.list_loaded_server_metadata())

# Ask the host to enumerate every available server (RPC call)
available = await runtime.list_servers()
print("Selectable via RPC:", available)

# Peek at tool docs before deciding to use them
loaded = runtime.list_loaded_server_metadata()
if loaded:
  description = runtime.describe_server(loaded[0]["name"])
  for tool in description["tools"]:
    print(tool["alias"], "→", tool.get("description", ""))
```

Typical output for the stub test server:

```
Discovered: ('stub',)
Loaded metadata: ({'name': 'stub', 'alias': 'stub', 'tools': [{'name': 'echo', 'alias': 'echo', 'description': 'Echo the provided message', 'input_schema': {...}}]},)
Selectable via RPC: ('stub',)
```

## Advanced Topics

### Custom Timeout Per Call

```python
# Set timeout for specific operation
result = await mcp_slow_service.long_operation(
    timeout=60  # Override default 30s
)
```

### Loading Specific Servers

```python
# Only load necessary servers
# When invoking run_python from your MCP client, specify the servers you need:
#   servers=['filesystem']
# Inside the sandboxed code you simply call the proxy:
result = await mcp_filesystem.read_file(path='/tmp/test.txt')
```

### Accessing Raw MCP Client

```python
# Direct server access
server = mcp_servers['filesystem']
result = await server.read_file(path='/tmp')
```

### Loading Specific Servers

**Note:** The `servers` parameter is only used when making the initial MCP tool call. The sandbox code sees only the proxies that were requested up front.

## Troubleshooting

### Startup throws `TypeError: 'async for' requires an object with __aiter__`

**Problem:**
```
TypeError: 'async for' requires an object with __aiter__ method, got Server
```

**Solution:**
You are likely running a pre-0.2.1 build that passed the server instance into
`stdio_server`. Upgrade to the latest release (or reinstall via `uvx
--from git+https://github.com/elusznik/mcp-server-code-execution-mode
mcp-server-code-execution-mode run`) and retry.

### Container Runtime Not Found

**Problem:**
```
Error: No container runtime found
```

**Solution:**
1. Install podman or docker
2. Verify: `podman --version`
3. Set explicit runtime: `export MCP_BRIDGE_RUNTIME=podman`

### Image Pull Failed

**Problem:**
```
Error: Failed to pull image python:3.12-slim
```

**Solution:**
```bash
# Manually pull
podman pull python:3.12-slim

# Or use different image
export MCP_BRIDGE_IMAGE=python:3.11-slim
```

### Gateway Servers Fail to Initialize

**Problem:**
```
failed to connect: calling "initialize": EOF
```

**Solution:**
1. Authenticate the Docker daemon with every registry referenced in the gateway catalog (run `docker login` for Docker Hub and `ghcr.io` as needed).
2. Ensure required secrets (for example `github.personal_access_token` for `github-official`) are set via `docker mcp secret set <name>` or your gateway's secrets backend.
3. Replicate any expected environment variables or volume mounts defined in the catalog so each server can find its configuration data.
4. Re-run the bridge and inspect the gateway logs; if `list_tools` only returns `mcp-add`/`code-mode`, the external servers still are not starting.

### Permission Denied

**Problem:**
```
Error: permission denied while trying to connect
```

**Solution:**
```bash
# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Or use podman (user namespaces)
podman info  # Verify user namespace
```

### Timeout Errors

**Problem:**
```
SandboxTimeout: Code exceeded timeout
```

**Solution:**
```bash
# Increase timeout
export MCP_BRIDGE_TIMEOUT=60

# Or per-call
result = await mcp_operation(timeout=60)
```

### Server Not Found

**Problem:**
```
Error: MCP server 'xyz' is not loaded
```

**Solution:**
1. Verify server in config: `~/.config/mcp/servers/*.json`
2. Check bridge logs for discovery messages
3. Restart bridge after adding server
4. Explicitly request server: `servers=['xyz']`

### Network Issues (In Container)

**Problem:**
```
Error: Network is unreachable
```

**Expected:** Containers have no network access by design.

**Solution:** Access resources via MCP servers only.

### Out of Memory

**Problem:**
```
Error: Memory limit exceeded
```

**Solution:**
```bash
# Increase memory limit
export MCP_BRIDGE_MEMORY=1g

# Or optimize code
# - Process data in chunks
# - Use generators
# - Clear references
```

### Too Many Processes

**Problem:**
```
Error: Cannot fork: Resource temporarily unavailable
```

**Solution:**
```bash
# Increase PID limit
export MCP_BRIDGE_PIDS=256

# Or reduce process count in code
```

### Slow Performance

**Problem:** Container startup is slow

**Solutions:**
1. Keep podman machine running (avoid shutdown)
2. Use local image: `podman pull python:3.12-slim`
3. Consider caching strategies
4. Reuse containers (not currently supported)

## Best Practices

### 1. Resource Management

```python
# GOOD: Process data in memory
data = await mcp_api.get_data()
processed = [item.transform() for item in data]

# BAD: Write large files to disk
await mcp_fs.write_file(path='/tmp/big.txt', data=huge_data)
```

### 2. Error Handling

```python
# GOOD: Handle errors gracefully
try:
    result = await mcp_api.operation()
except Exception as e:
    logger.error(f"Operation failed: {e}")
    return None

# BAD: No error handling
result = await mcp_api.operation()
```

### 3. Batching

```python
# GOOD: Batch requests
results = await asyncio.gather(
    mcp_api.call1(),
    mcp_api.call2(),
    mcp_api.call3()
)

# BAD: Sequential calls
r1 = await mcp_api.call1()
r2 = await mcp_api.call2()
r3 = await mcp_api.call3()
```

### 4. Timeouts

```python
# GOOD: Set appropriate timeouts
await mcp_fast_operation()              # Uses default 30s
await mcp_slow_operation(timeout=60)    # Explicit 60s

# BAD: Using default for all
await mcp_slow_operation()  # May timeout
```

### 5. Code Organization

```python
# GOOD: Modular code
def process_user_data(user_id):
    data = await mcp_api.get_user(user_id)
    return transform_user_data(data)

# Extract, transform, load
data = extract()
processed = transform(data)
await load(processed)

# BAD: Monolithic code
result = await mcp_api.call1()
result2 = await mcp_api.call2(result)
result3 = await mcp_api.call3(result2)
```

### 6. Security

```python
# GOOD: Use MCP servers for sensitive operations
await mcp_vault.get_secret('api_key')

# BAD: Hardcode secrets
API_KEY = "sk-1234567890abcdef"
```

### 7. Idempotency

```python
# GOOD: Idempotent operations
await mcp_api.upsert_record(id='123', data=updated_data)

# BAD: Non-idempotent
await mcp_api.create_record(id='123', ...)
await mcp_api.create_record(id='123', ...)  # Duplicate
```

### 8. Logging

```python
# GOOD: Log operations
logger.info(f"Processing {len(items)} items")
result = await mcp_api.batch_process(items)
logger.info(f"Completed: {result.count} processed")

# BAD: Silent operations
result = await mcp_api.batch_process(items)
```

### 9. Data Size

```python
# GOOD: Work with reasonable chunks
for batch in chunk_large_list(large_list, size=100):
    await mcp_api.process_batch(batch)

# BAD: Process everything at once
await mcp_api.process_batch(huge_list)
```

### 10. Cleanup

```python
# Container auto-cleans up, but:
# - Use temporary paths for files
# - Let context managers handle cleanup
# - Don't rely on persistent state

# Each execution is stateless
```

## Examples

See [README.md](README.md#usage-examples) for more examples.

## Support

For issues, questions, or contributions:
- Check [STATUS.md](STATUS.md) for roadmap
- Review [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
- See [HISTORY.md](HISTORY.md) for evolution
