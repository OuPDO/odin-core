FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (git noetig fuer scripts/reindex_repos.py clone/pull)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy source first so package dirs exist during install
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
