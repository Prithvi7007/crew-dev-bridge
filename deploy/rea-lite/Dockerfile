# Standalone REA Lite; image is not part of the CREW/wedding application.
FROM node:24.22.1-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --no-audit --no-fund rea-agents@6.8.0 \
    && npm cache clean --force

RUN mkdir -p /work/artifacts /work/evidence && chown -R node:node /work

USER node
WORKDIR /work
ENV HOME=/home/node \
    NODE_ENV=production \
    NODE_OPTIONS=--max-old-space-size=640 \
    REA_MCP_TOOL_FAMILIES=artifact,managed,application,session \
    REA_MCP_MAX_RESPONSE_BYTES=1048576 \
    MCP_ALLOWED_HOSTS=127.0.0.1,localhost

EXPOSE 18765
CMD ["rea","mcp","--http","--host","0.0.0.0","--port","18765"]
