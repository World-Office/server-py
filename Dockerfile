# opencloud-docserver — Stoic container
# One image, one process, one job.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv (small, fast, modern) via pip — avoids GHCR registry dependency
RUN pip install --no-cache-dir uv

# WeasyPrint (PDF export) system libraries + base fonts. Without the fonts,
# exported PDFs render blank glyphs — slim ships none.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 \
      fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first — layer caching means edits to src/ don't re-fetch deps
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code + static web assets
COPY src ./src
COPY web ./web
COPY config.toml ./

# Non-root user (Stoic: least privilege)
RUN useradd --create-home --uid 10001 docserver \
    && mkdir -p /app/data/documents \
    && chown -R docserver:docserver /app/data
USER docserver

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" || exit 1

CMD ["uv", "run", "--no-sync", "uvicorn", "--host", "0.0.0.0", "--port", "8000", "src.main:app"]
