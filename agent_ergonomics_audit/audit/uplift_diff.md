# Uplift Diff

- Median weighted score: 348 → 700 (**+352**)
- Largest uplift: runtime error contract (**+578**)
- First-try failures recovered:
  - bare invocation now returns useful help and exit 0
  - global options work before or after commands
  - `status`, `capture`, `screen`, and `--output` infer intent
  - typos name the exact corrected help command
- Canonical state + framebuffer loop: two serial processes → one `snapshot`
- Consumer-wrapper hot path: approximately 1.00 s → 0.16–0.18 s locally
- Physical HIL: `snapshot`, tap ACK, second snapshot, production restore passed

