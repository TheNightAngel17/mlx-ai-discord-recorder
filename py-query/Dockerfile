# py-query — RAG query API (CPU).
#
# Build context is the REPO ROOT (needs config.yaml at /app/config.yaml):
#   docker build -f py-query/Dockerfile -t mlx-query .

FROM python:3.11-slim
WORKDIR /app/py-query

COPY py-query/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY py-query/ ./
COPY config.yaml /app/config.yaml
# Built-in categories baked in; compose bind-mounts ./categories over it at runtime.
COPY categories/ /app/categories/

EXPOSE 8100
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
