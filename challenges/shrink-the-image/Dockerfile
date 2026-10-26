# ==============================================================================
# Stage 1: Build & Dependency Installation
# ==============================================================================
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .

# Install dependencies into the default root local bin
RUN pip install --no-cache-dir --user -r requirements.txt


# ==============================================================================
# Stage 2: Final Production Environment
# ==============================================================================
FROM python:3.13-slim AS runner

ENV PYTHONUNBUFFERED=1 \
    PATH=/home/appuser/.local/bin:$PATH

WORKDIR /app

# 1. Create a non-root system user and group safely
RUN groupadd -r appgroup && useradd -r -g appgroup -m -d /home/appuser appuser

# 2. Copy dependencies into the new user's home directory path
COPY --from=builder /root/.local /home/appuser/.local

# 3. Copy application files and grant ownership to the new user
COPY --chown=appuser:appgroup . .

# 4. Fix permissions for the copied local dependencies
RUN chown -R appuser:appgroup /home/appuser/.local

EXPOSE 8000

# 5. Switch to the non-root user
USER appuser

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
