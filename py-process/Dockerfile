# py-process — post-processing job runner (transcribe-assembly / merge / vectorize / summarize).
#
# Build context is the REPO ROOT (needs py-query/providers.py + config.yaml):
#   docker build -f py-process/Dockerfile -t mlx-process .
#
# LEAN image: no torch / openai-whisper. Live transcription (py-transcribe)
# produces a sidecar for every snippet, so transcribe.py never needs Whisper
# here; if a sidecar is ever missing it logs and skips (see transcribe.py).

FROM python:3.11-slim
WORKDIR /app/py-process

# ffmpeg is required by pydub (merge_audio.py)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Lean dependency set (NOT py-process/requirements.txt, which includes whisper).
RUN pip install --no-cache-dir \
    "PyYAML>=6.0" \
    "pydub>=0.25.1" \
    "chromadb>=0.5.0" \
    "requests>=2.31.0" \
    "python-dotenv>=1.0.0" \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0"

# Source. py-query is copied because vectorize.py imports its providers.py via
# sys.path (Path(__file__).parent.parent / "py-query").
COPY py-process/ ./
COPY py-query/ /app/py-query/
COPY config.yaml /app/config.yaml
# Built-in categories baked in; compose bind-mounts ./categories over it at runtime.
COPY categories/ /app/categories/

EXPOSE 8300
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8300"]
