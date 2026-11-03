FROM python:3.13-slim AS build

WORKDIR /app
COPY app.py requirements.txt .

RUN apt-get update
RUN apt-get install -y --no-install-recommends build-essential

RUN adduser slug
USER slug

RUN pip install --user -r requirements.txt

FROM python:3.13-slim AS runner

RUN adduser slug
USER slug

WORKDIR /app
COPY --from=build /app/app.py /app
COPY --from=build /home/slug/.local /home/slug/.local
ENV PATH=/home/slug/.local/bin:$PATH

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
