FROM python:3.11.18-alpine3.23

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt -r requirements-dev.txt

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
