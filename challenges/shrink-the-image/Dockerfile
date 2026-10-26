FROM python:3.13-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONUNBUFFERED=1

COPY requirements.txt ./

RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN addgroup -S appgroup && \
    adduser -S appuser -G appgroup && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
