# Offline, unprivileged static-analysis worker; separate from REA Lite and CREW.
FROM python:3.12-slim-bookworm

ARG JADX_VERSION=1.5.6
ARG JADX_SHA256=545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip openjdk-17-jre-headless binutils \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --location --retry 3 --output /tmp/jadx.zip \
       "https://github.com/skylot/jadx/releases/download/v${JADX_VERSION}/jadx-${JADX_VERSION}.zip" \
    && echo "${JADX_SHA256}  /tmp/jadx.zip" | sha256sum -c - \
    && mkdir -p /opt/jadx && unzip -q /tmp/jadx.zip -d /opt/jadx \
    && rm /tmp/jadx.zip \
    && test -x /opt/jadx/bin/jadx

COPY api.py /opt/rea-lab/api.py
RUN mkdir -p /results /work && chown -R 65534:1000 /results /work
USER 65534:1000
WORKDIR /opt/rea-lab
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8766
CMD ["python3", "/opt/rea-lab/api.py"]
