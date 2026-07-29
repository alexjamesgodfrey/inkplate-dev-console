# Phase 0 Scope Decision

- Target: `/Users/alexgodfrey/Developer/inkplate-dev-console`
- Tool: `inkplate-dev`
- Mode: `full`
- Primary agent profile: Codex CLI
- Orchestration tier: Solo (small CLI)
- CASS mining: disabled; no session mining or CASS-derived artifacts
- Triangulation: none
- Toolchain policy: do not install system packages without explicit approval
- Preflight fallback: macOS does not provide GNU `flock` or `timeout`; this
  solo pass avoids concurrent writers and uses bounded Python subprocesses for
  command timeouts.
- Compatibility guardrails:
  - Preserve all existing commands and environment variables.
  - Preserve the Python API and serial protocol.
  - Keep production firmware isolation guidance intact.
  - Scope changes to the standalone CLI/library, focused tests and docs, plus
    the existing `chessthing` convenience wrapper that consumes this tool.

