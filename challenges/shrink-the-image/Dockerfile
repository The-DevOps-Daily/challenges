FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential && \ 
    rm -rf /var/lib/apt/lists/*


WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONUNBUFFERED=1

COPY requirements.txt requirements-dev.txt ./

RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt 

COPY . .

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
