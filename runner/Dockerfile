# Build context must be a pinned checkout of crew_games, never the production app.
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/crew_requirements.txt
RUN python3 -m venv /opt/crew-venv && /opt/crew-venv/bin/pip install --no-cache-dir -r /tmp/crew_requirements.txt pytest
COPY frontend/package.json frontend/package-lock.json /opt/frontend/
RUN cd /opt/frontend && npm ci
WORKDIR /opt/worker
COPY runner/worker.py /opt/worker/worker.py
ENV PATH=/opt/crew-venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1
# Container supervisor runs as root with only narrowly scoped identity-switching caps.
# Every repo-controlled check is executed as 10002:10002 without capabilities.
USER 0:10001
CMD ["python", "/opt/worker/worker.py"]
