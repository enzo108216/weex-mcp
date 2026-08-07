# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEEX_MCP_PORT=8080

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir '.[server]'

USER 65532:65532
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port = os.environ.get('WEEX_MCP_PORT', '8080'); urllib.request.urlopen(f'http://127.0.0.1:{port}/readyz', timeout=2).read()"]

CMD ["weex-mcp", "serve", "--host", "0.0.0.0", "--workers", "1"]
