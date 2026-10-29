# REA Lite: private ChatGPT connection

The REA server uses unauthenticated HTTP on the VPS loopback at `http://127.0.0.1:18765/mcp`. Do not open an inbound public port, use an unauthenticated public tunnel, or include API keys in source control.

Once `python3 /opt/rea-lite/scripts/smoke_test.py` succeeds, consult the current official OpenAI Secure MCP Tunnel documentation:

https://developers.openai.com/api/docs/guides/secure-mcp-tunnels

If the user's Platform organization and ChatGPT workspace support the capability, create a private tunnel, install the official tunnel client under an unprivileged dedicated account on the VPS, and point its local MCP destination to `http://127.0.0.1:18765/mcp`. Follow official tunnel credential storage instructions; do not paste tokens into chat or commit credentials. The tunnel operates outbound over HTTPS; the REA listener remains bound to loopback.

Validate the tunnel's tool discovery and call `binary_session` with an empty argument object. Only after that should an authorized test artifact be investigated. Avoid registering multiple simultaneous clients against REA's stateful, one-client HTTP transport.

A secure tunnel does not remove REA's file/process access risks: do not make runtime, device, or arbitrary-execution tools available to untrusted users.
