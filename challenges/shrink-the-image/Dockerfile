FROM python:3.13-alpine

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

RUN adduser -D shrink
USER shrink

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
