# Starter Dockerfile, unchanged apart from this line.
FROM python:3.13

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    vim

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt -r requirements-dev.txt

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
