# Pass 1 Ergonomics Playbook

The tool's serial protocol and frame encoding are compact and sound. The main
weakness is the automation surface around them: uncaught operational failures,
fragile option placement, no local diagnostics, no one-call state+frame loop,
minimal help, and a consuming shell wrapper that can fail before invoking the
CLI.

## Apply order

1. **R-001 + R-006:** establish typed operational failures, stable exit codes,
   deterministic port selection, and actionable recovery commands.
2. **R-002:** make bare invocation, global-option placement, aliases, and typo
   recovery match first-try agent intent.
3. **R-003:** add `ports` and `doctor` so port/environment failures can be
   diagnosed without a serial transaction.
4. **R-004:** add `snapshot` to collapse the canonical state/frame loop into
   one serial connection.
5. **R-005:** add capabilities, robot docs, version, rich help, and schema
   contracts.
6. **R-007:** replace the `chessthing` consuming wrapper with a fast, robust,
   fully tested resolver.
7. **R-008:** pin everything with CLI, client, packaging, wrapper, and physical
   hardware verification.

## Compatibility stance

All existing commands, Python APIs, environment variables, and serial protocol
messages remain valid. New behavior is additive except that expected failures
become concise stable errors instead of Python tracebacks, and `INKPLATE_PORT`
now correctly takes precedence over the generic `UPLOAD_PORT` as documented.

