# One image: the web UI built with node, served by sopops (FastAPI) with the sopc compiler.

FROM node:22-slim AS web
RUN corepack enable
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim

# sopc compiles the prompts; pinned so served prompts don't change under a rebuild.
ARG SOPC_REF=v0.0.18
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://raw.githubusercontent.com/amanmibra/sopc/main/install.sh | SOPC_REF=$SOPC_REF SOPC_INSTALL_DIR=/usr/local/bin sh \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Dependencies from uv.lock, every one checked against its sha256 (--require-hashes): no unpinned upgrades.
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
# Extras: postgres, hatchet (the worker), livekit (no-code LiveKit capture: join a room and read its transcript).
RUN uv export --frozen --no-dev --no-emit-project --extra postgres --extra hatchet --extra livekit -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt /usr/local/bin/uv
COPY README.md hatch_build.py ./
COPY src ./src
COPY skills ./skills
COPY --from=web /web/dist ./web/dist
# The build hook bundles web/dist into the package as sopops_server/static, and the onboarding skill (skills/) with it.
RUN pip install --no-cache-dir --no-deps . && rm -rf web skills

# Set DATABASE_URL to Postgres in production; the SQLite default lives in the /data volume.
# Migrations run on startup.
RUN mkdir /data && chown nobody /data
VOLUME /data
ENV HOST=0.0.0.0 PORT=8484 SOPC_BIN=/usr/local/bin/sopc DATABASE_URL=sqlite:////data/sopops.db
EXPOSE 8484
USER nobody
CMD ["sopops"]
