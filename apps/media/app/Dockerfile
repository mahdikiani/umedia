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

RUN apt-get update \
    && apt-get install -y libmagic1 rclone \
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
