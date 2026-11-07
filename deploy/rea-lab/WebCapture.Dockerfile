FROM python:3.12-slim-bookworm
# Web fetching is isolated from the offline REA evidence worker.
COPY web_capture.py /opt/rea-web/web_capture.py
USER 65534:1000
WORKDIR /opt/rea-web
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8767
CMD ["python3", "/opt/rea-web/web_capture.py"]
