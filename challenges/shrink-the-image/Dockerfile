# ==============================================================================
# Stage 1: Build & Dependency Installation
# ==============================================================================
FROM python:3.13-slim AS builder

# Install compilers and system dependencies needed for building Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only dependency files first to maximize Docker layer caching
COPY requirements.txt .

# Install dependencies into a dedicated local directory
RUN pip install --no-cache-dir --user -r requirements.txt


# ==============================================================================
# Stage 2: Final Production Environment
# ==============================================================================
FROM python:3.13-slim AS runner

# Set production environment variables
ENV PYTHONUNBUFFERED=1 \
    PATH=/root/.local/bin:$PATH

WORKDIR /app

# Copy installed Python packages from the builder stage
COPY --from=builder /root/.local /root/.local

# Copy application source code
COPY . .

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
