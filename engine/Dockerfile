# API image. Build from the repository root:
#   docker build -f engine/Dockerfile -t puckiq-api .
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PUCKIQ_ARTIFACT_DIR=/app/artifacts \
    PUCKIQ_DATA_DIR=/app/data \
    PUCKIQ_LOG_JSON=1
WORKDIR /app
COPY engine/ engine/
RUN pip install --no-cache-dir "./engine[postgres]"
# Tuned rating parameters and the backtest report the Accuracy page serves.
COPY artifacts/ artifacts/
RUN useradd --create-home puckiq && mkdir -p /app/data && chown -R puckiq /app
USER puckiq
EXPOSE 8000
CMD ["sh", "-c", "uvicorn puckiq.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
