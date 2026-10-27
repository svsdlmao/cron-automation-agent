# Time-to-execution: rules

50 runs (50 installed), file backend, instant approval.

- Server start + MCP handshake: 1.026 s (once per session)
- Request → installed and verified: median 0.0219 s, 95th percentile 0.0328 s
- of which planning: median 0.0002 s; MCP tool calls (lookup, dry run, write, verify): median 0.0217 s
