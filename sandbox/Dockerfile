# The sandbox service of code mode (M37): it runs the scripts an agent writes, and nothing else.
#
# Deno is installed from its release at a pinned version. The zip is downloaded here, at build time,
# and checked against the SHA-256 that the release publishes next to it (the `.zip.sha256sum` files
# of https://github.com/denoland/deno/releases/tag/v2.9.8, read again on 2026-11-21 and found equal
# to the values below). Nothing is downloaded when the container runs, and the container has no
# network at all (see compose.yaml).
FROM python:3.12-slim AS fetch
ARG TARGETARCH
ARG DENO_VERSION=2.9.8
ARG DENO_SHA256_AMD64=e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609
ARG DENO_SHA256_ARM64=d49596c20b9788cf8f5d8ddf644103d4bde56ed239753f0bdb92ba9361d51905
COPY fetch_deno.py /fetch_deno.py
RUN arch="${TARGETARCH:-$(dpkg --print-architecture)}" \
 && case "$arch" in \
      amd64) target=x86_64-unknown-linux-gnu; sha="$DENO_SHA256_AMD64" ;; \
      arm64) target=aarch64-unknown-linux-gnu; sha="$DENO_SHA256_ARM64" ;; \
      *) echo "No pinned Deno build for the architecture $arch." >&2; exit 1 ;; \
    esac \
 && python /fetch_deno.py "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-${target}.zip" "$sha" /out

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=fetch /out/deno /usr/local/bin/deno
# The sandbox runs as uid 10002 (the app is 10001). Group 10003 is shared with the app (compose.yaml
# adds it to the app), so the app can use the socket in /run/sandbox, which belongs to that group.
RUN groupadd --system --gid 10003 vrum-sandbox \
 && useradd --system --no-create-home --uid 10002 --gid 10003 --shell /usr/sbin/nologin sandbox \
 && mkdir /run/sandbox \
 && chown 10002:10003 /run/sandbox \
 && chmod 2770 /run/sandbox
WORKDIR /sandbox
COPY supervisor.py prepare.ts runner.ts prelude.d.ts deno.json import_map.json check_import_map.ts ./
# Build-time checks: the programs that run scripts are type checked, and the import map must stub every
# node: module this Deno has (a script must not reach one of them), or the build fails.
RUN export DENO_DIR=/tmp/deno-build \
 && deno check --no-config --no-lock --no-remote runner.ts prepare.ts check_import_map.ts \
 && deno run --no-prompt --no-remote --no-npm --no-config --no-lock --allow-read=/sandbox/import_map.json \
    check_import_map.ts /sandbox/import_map.json \
 && rm -rf /tmp/deno-build
USER sandbox
HEALTHCHECK --interval=5s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python", "/sandbox/supervisor.py", "ping"]
CMD ["python", "/sandbox/supervisor.py"]
