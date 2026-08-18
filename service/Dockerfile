# CUDA runtime sized for an 8 GB card. For CPU-only, swap the base for python:3.11-slim
# and install the CPU torch wheels.
FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ARTIFACTS_DIR=/app/artifacts \
    OMP_NUM_THREADS=4

WORKDIR /app

# libGL/libglib are needed by opencv, which ultralytics pulls in
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# torch/torchvision already live in the base image
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
