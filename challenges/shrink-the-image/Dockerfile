FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt app.py .

RUN pip install --no-cache-dir -r requirements.txt && \
useradd --uid 1001 app

USER 1001

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
