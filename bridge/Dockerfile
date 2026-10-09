FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY bridge/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY bridge/ /app/bridge/
ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["python", "-m", "bridge.server"]
