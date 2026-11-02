FROM python:3.13-slim AS build
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/deps -r requirements.txt

FROM gcr.io/distroless/python3-debian13:nonroot
WORKDIR /app
COPY --from=build /deps /deps
COPY app.py .
ENV PYTHONPATH=/deps
EXPOSE 8000
CMD ["-m", "gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
