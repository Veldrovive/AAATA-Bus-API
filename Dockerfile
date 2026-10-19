FROM python:3.12-slim-trixie

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Configure environment:
# - UV_COMPILE_BYTECODE: Compile Python files to bytecode for faster startup
# - UV_LINK_MODE=copy: Ensures files are copied instead of hardlinked across mount points
# - PYTHONUNBUFFERED=1: Flushes stdout/stderr immediately so Docker logs display real-time
# - AAATA_DATA_PATH: Default data folder in container, mounted to host ./dat
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    AAATA_DATA_PATH=/app/dat \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first for Docker layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy project files and entrypoint
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

COPY . .

# Install the current project package (aaata-bus)
RUN uv sync --frozen

# Create data directory
RUN mkdir -p /app/dat

ENTRYPOINT ["/app/docker-entrypoint.sh"]

CMD ["collect_data.py"]
