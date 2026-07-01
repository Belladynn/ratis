# Postgres 16 + PostGIS 3. Image construite localement : postgres:16 est
# multi-arch (arm64 dev/CI + x86 prod) alors que postgis/postgis est
# amd64-only — éviter l'émulation sur le Mac mini arm64.
#
# Base pinned by digest (Dependabot package-ecosystem: docker bumps it). The
# upstream postgres image already runs as the unprivileged `postgres` user and
# drops privileges via its entrypoint — we keep that USER semantics untouched.
FROM postgres:18@sha256:4aabea78cf39b90e834caf3af7d602a18565f6fe2508705c8d01aa63245c2e20
# DL3008: postgis version intentionally unpinned — it tracks the postgres:16 base,
# which is digest-pinned here (freezing the apt snapshot); Dependabot bumps the digest.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
    && rm -rf /var/lib/apt/lists/*
