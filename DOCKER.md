# Running with Docker

The whole stack runs as four containers (plus an optional local Ollama), wired
by `docker-compose.yml`. The bot is pure Node; the three Python pieces are
FastAPI services.

```
┌────────────┐   enqueue snippet (HTTP)   ┌──────────────────┐
│   bot      │ ─────────────────────────► │  py-transcribe   │  (GPU, warm Whisper)
│  (Node)    │                            └──────────────────┘
│            │   start/poll job (HTTP)    ┌──────────────────┐
│            │ ─────────────────────────► │   py-process     │  (transcribe→merge→vectorize→summarize)
│            │   ask (HTTP)               ├──────────────────┤
│            │ ─────────────────────────► │   py-query       │  (RAG API)
└────────────┘                            └──────────────────┘

Shared named volumes (mounted at identical paths in every container):
  recordings  -> /data/recordings   bot (rw), py-transcribe (rw), py-process (rw)
  vectordb    -> /data/vectordb      py-process (rw), py-query (rw)
  whisper-cache -> /data/model-cache py-transcribe (rw)   # persists model downloads
  ollama-models -> /root/.ollama     ollama (rw, profile)
```

> **Why identical mount paths?** The recorder hands py-transcribe an **absolute
> `wav_path`**. It only resolves in the transcribe container because `recordings`
> is mounted at the same `/data/recordings` in both. Keep that invariant.

## Prerequisites

- **Docker Engine + Compose v2** (`docker compose version`).
- **NVIDIA Container Toolkit** on the host for the GPU services (`py-transcribe`,
  and `ollama` if used). Verify with:
  `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`.

## Setup

```bash
cp .env.example .env          # fill in DISCORD_TOKEN, GUILD_ID, and any *_API_KEY
# Review deploy/config.docker.yaml — it overlays config.yaml inside the containers
# (data paths -> /data/..., service URLs -> service names). Bare-metal config.yaml
# is left untouched.
```

Secrets are injected via `env_file: ./.env`; they are **never** baked into images.

## Build & run

```bash
docker compose build                  # build all four images
docker compose up -d                  # bot + py-process + py-query + py-transcribe
docker compose logs -f bot            # follow the bot
docker compose --profile ollama up -d # also start a local Ollama (GPU)
```

Bring up a subset (e.g. no GPU on this box yet):

```bash
docker compose up -d py-query py-process bot   # py-transcribe is reached
                                               # fire-and-forget; bot won't block on it
```

Ports are published to `127.0.0.1` only (8100 query, 8200 transcribe, 8300
process) for local debugging; service-to-service traffic uses the Compose
network by name regardless.

## Common operations

```bash
docker compose ps
docker compose restart py-process
docker compose build py-transcribe && docker compose up -d py-transcribe
docker compose exec py-transcribe ls -R /data/recordings   # inspect shared volume
docker compose down                # stop (volumes persist)
docker compose down -v             # stop AND delete volumes (recordings + vectordb!)
```

## Bare-metal still works

None of this changes the non-Docker workflow: `config.yaml` keeps its local paths,
the service URLs default to `http://localhost:<port>`, and you can run each service
with `uvicorn` / `node bot.js` as before.

## Troubleshooting

- **`py-transcribe` unhealthy at first:** the model downloads on first start;
  the healthcheck has a 180s `start_period`. Watch `docker compose logs -f
  py-transcribe`. Downloads persist in the `whisper-cache` volume.
- **cuDNN / CUDA load error from faster-whisper:** ctranslate2, cuDNN and the CUDA
  base image are version-coupled. If you see a cuDNN load failure, align the base
  image in `py-transcribe/Dockerfile` with the cuDNN major version ctranslate2
  expects (see the faster-whisper README).
- **ChromaDB locking:** py-process (writer) and py-query (reader) share the sqlite
  -backed `vectordb` volume. Fine on one host; see the k3s notes for multi-node.

## Toward k3s (k3s evolution)

This Compose maps cleanly onto k3s:

- **Volumes → PVCs.** `recordings` must be **ReadWriteMany** (bot + py-transcribe +
  py-process all mount it); `vectordb` likewise if py-process and py-query land on
  different nodes. Use an RWX storage class (NFS, Longhorn, etc.).
- **`deploy/config.docker.yaml` → a ConfigMap** mounted at `/app/config.yaml`.
- **`.env` → a Secret** surfaced as env vars.
- **GPU** via the NVIDIA device plugin + `nvidia.com/gpu` resource requests on the
  `py-transcribe`/`ollama` Deployments.
- **bot stays a single replica** (one Discord gateway connection per token).
- **ChromaDB on RWX with two writers is the weak point** — the clean multi-node
  move is a **Chroma server** Deployment that py-process and py-query talk to over
  HTTP (swap `chromadb.PersistentClient` for `HttpClient`), removing shared-file
  access entirely.
