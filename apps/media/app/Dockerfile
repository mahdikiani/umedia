FROM python:3.13-slim AS fast-uv

WORKDIR /app

RUN pip install --no-cache-dir uv==0.7.10

COPY pyproject.toml pyproject.toml

# RUN uv sync --no-dev --no-cache --no-install-project
RUN uv pip install --system --no-cache-dir .


FROM python:3.13-slim AS fast-server

# Copy installed site-packages from build stage
COPY --from=fast-uv /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

WORKDIR /app

# rclone comes from the official release, pinned + checksum-verified, not
# apt: Debian trixie ships 1.60.1, which predates the sftp `host_keys`
# option (1.75) that pins SFTP server keys -- an old binary would silently
# skip host-key verification. Checksums: downloads.rclone.org/v1.75.1/SHA256SUMS.
ARG RCLONE_VERSION=1.75.1
ARG TARGETARCH=amd64
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 ca-certificates curl unzip \
    && case "$TARGETARCH" in \
         amd64) sum=982b5aa772841168f8e380f139e9e787b2a105403e32b94da8676a0e1c0a13ab ;; \
         arm64) sum=03f2504174034b6d004152ed7369251c9a9ec1f7e0836eda420f5c7a5ec0dff9 ;; \
         *) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/rclone.zip \
         "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-${TARGETARCH}.zip" \
    && echo "$sum  /tmp/rclone.zip" | sha256sum -c - \
    && unzip -j /tmp/rclone.zip "*/rclone" -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/rclone \
    && rm /tmp/rclone.zip \
    && apt-get purge -y curl unzip && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 1000 user \
    && adduser  --system --uid 1000 --ingroup user --disabled-password --gecos '' --home /home/user user \
    && mkdir -p /app/logs /data /storage /run/umedia/plugins \
    && chown -R user:user /app/logs /data /storage /run/umedia
# /data: named volume mounts inherit an image directory's ownership on
# first creation, so this must exist (owned by `user`) before the volume
# attaches -- otherwise it mounts root:root and the app (deliberately
# non-root, unlike apps/api's Dockerfile) can't write its own SQLite file.
# /run/umedia/plugins: plugin_socket_dir (server/config.py) -- not a
# volume, just needs to be creatable; /run itself is root:root in the
# base image.

USER user
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUTF8=1
# Copy application code
COPY --chown=user:user . .

ENTRYPOINT [ "./docker-entrypoint.sh" ]
