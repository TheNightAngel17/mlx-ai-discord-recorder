# js-bot — Discord recorder bot (pure Node).
#
# Build context is the REPO ROOT (the image needs config.yaml at /app/config.yaml):
#   docker build -f js-bot/Dockerfile -t mlx-bot .
#
# Two stages: native modules (sodium-native, etc.) are built on the full image
# that has toolchain available, then copied into a slim runtime.

# ---- build stage: install + compile node deps ----
FROM node:20-bookworm AS build
WORKDIR /app/js-bot
COPY js-bot/package*.json ./
# --legacy-peer-deps: prism-media declares an *optional* peer of opusscript@^0.0.8,
# but the project uses opusscript@^0.1.1 (works fine in practice). npm 7+ otherwise
# fails the resolve with ERESOLVE. --no-audit/--no-fund quiet the build output.
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
