# js-bot — Discord recorder bot (pure Node).
#
# Build context is the REPO ROOT (the image needs config.yaml at /app/config.yaml):
#   docker build -f js-bot/Dockerfile -t mlx-bot .
#
# Two stages: native modules (sodium-native, @discordjs/opus) are built on the
# full image that has toolchain available, then copied into a slim runtime.

# ---- build stage: install + compile node deps ----
FROM node:20-bookworm AS build
WORKDIR /app/js-bot
# Native deps ship prebuilt binaries but fall back to compiling from source when
# no prebuild matches this platform/glibc (common for @discordjs/opus), so make
# sure the toolchain node-gyp needs is present. This lives only in the throwaway
# build stage — the slim runtime image below stays small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY js-bot/package*.json ./
# We use the native @discordjs/opus decoder rather than the pure-WASM opusscript:
# opusscript aborts the whole process (Emscripten abort -> "memory access out of
# bounds") on malformed Opus packets, whereas @discordjs/opus raises recoverable
# JS errors. --legacy-peer-deps: prism-media declares several *optional* opus peer
# deps; npm 7+ can otherwise fail the resolve with ERESOLVE. --no-audit/--no-fund
# quiet the build output.
RUN npm install --omit=dev --legacy-peer-deps --no-audit --no-fund

# ---- runtime stage: slim ----
FROM node:20-bookworm-slim
WORKDIR /app/js-bot
ENV NODE_ENV=production
COPY --from=build /app/js-bot/node_modules ./node_modules
COPY js-bot/ ./
# Default config baked in; compose bind-mounts deploy/config.docker.yaml over it.
COPY config.yaml /app/config.yaml
# Built-in categories baked in; compose bind-mounts ./categories over it (rw) so the
# bot's /mlx-ai category writes persist back to the host.
COPY categories/ /app/categories/
CMD ["node", "bot.js"]
