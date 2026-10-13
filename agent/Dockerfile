FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY healer/ healer/

# Distroless-style non-root UID; the deployment also sets runAsNonRoot.
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["python", "-m", "healer"]
