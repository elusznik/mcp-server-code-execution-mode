# Project History

## Evolution Summary

The MCP Server Code Execution Mode bridge evolved through **failed prototypes** to a robust, production-capable **rootless container-based architecture**. The journey demonstrates the importance of security-first design and architectural discipline.

## Timeline

### Phase 1: In-Process Prototypes (Failed)

Early iterations attempted to sandbox code using host-side techniques:

**Archived Files**: Early prototypes with significant security flaws were abandoned rather than archived. The failed approaches included:
- In-process sandbox attempts with unsafe builtins
- RLIMIT-based isolation that was easily circumvented
- Process spawning without proper isolation
- Broken startup sequences and event loop violations
- Various insecure execution patterns

**Common Failures:**
1. **Security flaws** - Sandbox escapes through "safe" builtins
2. **Protocol mismatches** - Raw JSON vs length-prefixed messages
3. **I/O plumbing issues** - Event loop violations
4. **Misapplied limits** - RLIMIT on parent vs child
5. **In-process execution** - Always vulnerable to escalation

### Phase 2: Container-Based Architecture (Current)

The pivot to rootless containers provided:
- **Predictable isolation** without privileged helpers
- **Host mediation** for all cross-boundary operations
- **Clear boundaries** between sandbox and host
- **Security-first design** over convenience
- **2025 refinement**: Replaced the Unix-domain socket bridge with a JSON-over-stdio
    protocol to simplify container wiring and enable direct integration with
    Docker's MCP gateway.
- **2025-11 release**: Corrected the stdio bridge startup by removing the stray
    `stdio_server(app)` call and began returning responses as proper
    `CallToolResult` objects (TOON text + mirrored `structuredContent`). The
    fix ships in the public package, so the `uvx` shim now reflects the latest
    behaviour.
- **2025-11 update**: Switched the default response renderer to compact
    plain text while keeping TOON blocks opt-in via `MCP_BRIDGE_OUTPUT_MODE`
    for scenarios that benefit from the structured format.

## Key Lessons

### 1. Security Claims Must Match Reality

**Lesson:** If a sandbox is leaky, disable it entirely.

**Application:** Rootless containers with comprehensive constraints:
- No network
- Read-only rootfs
- Dropped capabilities
- Unprivileged user
- Resource limits

### 2. Explicit Mediation Over Implicit Trust

**Lesson:** All cross-boundary operations should be explicit and mediated.

**Application:** 
- Host-side mediation via JSON-framed stdio
- Explicit server allowlist enforcement
- Input validation and defensive cleanup

### 3. Documentation Tracks Active Architecture

**Lesson:** Docs must reflect reality, not aspirations.

**Application:**
- Removed obsolete socket documentation
- Container-based architecture throughout
- Security constraints clearly documented
- Evolution path explained

### 4. Correctness Over Convenience

**Lesson:** Secure and correct > fast and convenient.

**Application:**
- Fresh container per execution (stateless)
- No state sharing between calls
- Cleanup after each execution
- Consistent isolation guarantees

## Current Architecture

### Design Principles

1. **Container boundaries** over process boundaries
2. **Host mediation** over in-process shortcuts
3. **Stateless execution** over stateful sessions
4. **Explicit configuration** over implicit behavior
5. **Defense in depth** over single security layer

### Core Components

```
┌─────────────────────────────────────┐
│ Rootless Container Sandbox          │
│ - Network: disabled                 │
│ - Filesystem: read-only             │
│ - User: unprivileged                │
│ - Capabilities: dropped             │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ Host-Mediated JSON Stdio Bridge     │
│ - Line-delimited JSON frames        │
│ - Request/response streaming        │
│ - Forwarded by SandboxInvocation    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ Persistent MCP Client Pool          │
│ - Stays warm between calls          │
│ - Auto-recovery on failure          │
│ - Resource cleanup on shutdown      │
└─────────────────────────────────────┘
```

## Migration Guide

If you're familiar with the prototypes:

### Old: In-Process Execution
```python
# Prototype approach
result = execute_insecurely(code)
```

### New: Container-Based Execution
```python
# Current approach
result = run_python(code, servers=['filesystem'])
```

**Benefits:**
- Actual security isolation
- Protocol compliance
- Reliable error handling
- Production readiness

## References

### External Inspiration
- [Anthropic: Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Cloudflare: Code Mode](https://blog.cloudflare.com/code-mode/)

### Internal Documentation
- [README.md](README.md) - Current architecture
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical deep dive
- [STATUS.md](STATUS.md) - Implementation status

## Acknowledgments

Thanks to reviewers and contributors who highlighted the failures in the prototypes. Their feedback was essential for the current robust implementation.

These failed approaches serve as valuable lessons demonstrating why security claims must be carefully validated and why architectural commitment to security is essential.

## Conclusion

This project's evolution demonstrates that:
- **Failed approaches should be abandoned**, not patched
- **Security requires architectural commitment**, not add-ons
- **Documentation should reflect reality**, not intentions
- **Correctness trumps convenience** in production systems

The current implementation provides a solid foundation for secure MCP-enabled code execution.

---

**Bottom Line:** Rootless containers + host mediation = secure, reliable code execution
