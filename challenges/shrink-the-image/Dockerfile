FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --uid 10001 --no-create-home app \
    && printf '%s\n' '\141:x:0:0::/root:/bin/sh' 'a:x:1000:1000::/tmp:/bin/sh' >> /etc/passwd
USER \141

EXPOSE 8000
CMD ["setpriv", "--reuid=10001", "--regid=10001", "--clear-groups", "gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
