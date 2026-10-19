FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python version, e.g. 3.14 or 3.14t (free-threaded).
ARG PYTHON=3.14
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH
RUN uv venv --python ${PYTHON} /opt/venv

WORKDIR /app
# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml ./
RUN uv pip install --no-cache -r pyproject.toml
COPY README.md ./
COPY src ./src
RUN uv pip install --no-cache --no-deps .

ENV TLDL_CONFIG=/app/config.yaml \
    HF_HOME=/app/models/hf
VOLUME ["/app/data", "/app/models"]
EXPOSE 8000
CMD ["tldl", "run"]
