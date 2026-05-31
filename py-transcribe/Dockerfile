# py-transcribe — warm-model live transcription service (GPU).
#
# Build context is the REPO ROOT (needs config.yaml at /app/config.yaml):
#   docker build -f py-transcribe/Dockerfile -t mlx-transcribe .
#
# CUDA + cuDNN runtime base so faster-whisper (ctranslate2) can use the GPU.
# NOTE: ctranslate2 / cuDNN / CUDA versions are coupled — if faster-whisper logs
# a cuDNN/CUDA load error, align this base image with the ctranslate2 build's
# required cuDNN major version (see faster-whisper docs).

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive \
    HF_HOME=/data/model-cache \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/py-transcribe

RUN pip3 install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "PyYAML>=6.0" \
    "python-dotenv>=1.0.0" \
    "faster-whisper>=1.0.0"

COPY py-transcribe/ ./
COPY config.yaml /app/config.yaml

EXPOSE 8200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
