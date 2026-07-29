# Pre-pass canonical task simulation

| Task | First command | Result | Round trips |
| --- | --- | --- | --- |
| Discover the CLI | `inkplate-dev` | Exit 2; required-command error, no examples | 2+ |
| Select a port naturally | `inkplate-dev state --port /dev/null` | Exit 2; global option rejected after command | 2 |
| Ask for status | `inkplate-dev status` | Exit 2; no alias or exact correction | 2 |
| Capture with conventional output flag | `inkplate-dev frame --output /tmp/frame.png` | Exit 2 | 2 |
| Diagnose port selection | `inkplate-dev doctor --json` | Command absent | External source lookup |
| Read state and screen | `state`, then `frame` | Two processes and two serial opens | 2 |

