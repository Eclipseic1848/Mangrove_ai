FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/ ./
RUN npm ci && npm run build

FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS toolchain-build

WORKDIR /tmp/mangrove-tools
ADD --checksum=sha256:c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74 https://github.com/sigstore/cosign/releases/download/v3.0.6/cosign-linux-amd64 ./cosign
ADD --checksum=sha256:9229ccc6d17bb282039ad4a69abb16dcb887a5bce567c075d731d9b3c7ad8eaf https://github.com/oras-project/oras/releases/download/v1.3.2/oras_1.3.2_linux_amd64.tar.gz ./oras.tar.gz
ADD --checksum=sha256:8b4376d5d6befe5c24d503f10ff136d9e0c49f9127a4279fd110b727929a5aa9 https://github.com/aquasecurity/trivy/releases/download/v0.70.0/trivy_0.70.0_Linux-64bit.tar.gz ./trivy.tar.gz
ADD --checksum=sha256:bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788 https://github.com/anchore/syft/releases/download/v1.50.0/syft_1.50.0_linux_amd64.tar.gz ./syft.tar.gz
RUN set -eux; \
    mkdir -p /opt/mangrove-tools /tmp/oras /tmp/trivy /tmp/syft; \
    install -m 0555 cosign /opt/mangrove-tools/cosign; \
    tar -xzf oras.tar.gz -C /tmp/oras; \
    tar -xzf trivy.tar.gz -C /tmp/trivy; \
    tar -xzf syft.tar.gz -C /tmp/syft; \
    install -m 0555 /tmp/oras/oras /opt/mangrove-tools/oras; \
    install -m 0555 /tmp/trivy/trivy /opt/mangrove-tools/trivy; \
    install -m 0555 /tmp/syft/syft /opt/mangrove-tools/syft; \
    echo 'c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74  /opt/mangrove-tools/cosign' | sha256sum -c -; \
    echo '22dc6b05994a786f36bc57de47c48562543f3fa0cc62366a9a9574b422a13ab2  /opt/mangrove-tools/oras' | sha256sum -c -; \
    echo '379d59f24a4a828c55de5f0b91b6805cc35d13580180b658820e648611256166  /opt/mangrove-tools/trivy' | sha256sum -c -; \
    echo '22f2b95baf524d45ad16b0ad5cdeb200c4b8a816493768cec50e4682b1f24b0e  /opt/mangrove-tools/syft' | sha256sum -c -

FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS python-build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN --mount=type=cache,id=phase4b-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,id=phase4b-apt-cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 install -y --no-install-recommends \
        build-essential \
        git

COPY requirements.txt requirements-collectors.txt ./
RUN --mount=type=cache,id=phase4b-pip-cache,target=/root/.cache/pip,sharing=locked \
    python -m venv /opt/venv \
    && python -m pip install --upgrade pip==26.2 \
    && python -m pip install -r requirements.txt -r requirements-collectors.txt \
    && python -m playwright install chromium

FROM python:3.13-slim-bookworm@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CAPABILITY_SUPPLY_CHAIN_TOOL_ROOT=/opt/mangrove-tools \
    CAPABILITY_SUPPLY_CHAIN_LOCK_PATH=/app/config/supply-chain-tools.linux-amd64.lock.json \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=python-build /opt/venv /opt/venv
COPY --from=python-build /ms-playwright /ms-playwright

RUN --mount=type=cache,id=phase4b-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    --mount=type=cache,id=phase4b-apt-cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 install -y --no-install-recommends \
        ffmpeg \
        git \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libmagic1 \
        poppler-utils \
    && python -m playwright install-deps chromium

COPY --from=toolchain-build /opt/mangrove-tools /opt/mangrove-tools

RUN chmod -R a+rX /ms-playwright \
    && groupadd --gid 10001 mangrove \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/mangrove mangrove \
    && mkdir -p /app/data /app/logs \
    && chown -R 10001:10001 /app/data /app/logs /home/mangrove

COPY src ./src
COPY config ./config
COPY skills ./skills
COPY docker/phase4b/entrypoint.sh /app/docker/phase4b/entrypoint.sh
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

RUN chmod 0555 /app/docker/phase4b/entrypoint.sh

USER 10001:10001
EXPOSE 8088
ENTRYPOINT ["/app/docker/phase4b/entrypoint.sh"]
CMD ["python", "-m", "src.api.main"]
