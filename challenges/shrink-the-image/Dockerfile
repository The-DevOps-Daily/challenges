FROM python:3.13-alpine

WORKDIR /app

RUN adduser -D -u 10001 appuser # Alpine uses adduser instead of useradd

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

USER appuser

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
