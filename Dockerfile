# syntax=docker/dockerfile:1

FROM python:3.11-slim

# ffmpeg is required by py-cord for voice audio processing
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (separate layer for caching efficiency)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project files
COPY . .

# Secrets are injected at runtime via --env-file .env or Kubernetes Secrets.
# Do NOT bake DISCORD_TOKEN or GUILD_ID into the image.
ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
