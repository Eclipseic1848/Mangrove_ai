# 构建前后核对本地 Pi 工具链内容身份；不覆盖 Pi 镜像或重新下载解析工具。
ARG TOOLCHAIN_IMAGE=mangrove/pi-coding-agent:0.80.10@sha256:a241e5e428195a3f979f84a4ffaa67ad728c4f0cb9fb48839420fe268aaf4316
FROM ${TOOLCHAIN_IMAGE}

# 构建上下文仅为已校验的 wheel 目录，不包含仓库、业务来源或宿主凭据。
COPY coremind_ai-0.7.1-py3-none-any.whl /tmp/coremind_ai-0.7.1-py3-none-any.whl
RUN python -c "import hashlib,pathlib,sys; p=pathlib.Path('/tmp/coremind_ai-0.7.1-py3-none-any.whl'); sys.exit(0 if hashlib.sha256(p.read_bytes()).hexdigest() == '3fa5301c444da2e3bdaca51bd4800b1bdbcb6dc68e3abef4b39197bde3625e74' else 'CoreMind wheel digest mismatch')" \
    && python -m pip --isolated install --disable-pip-version-check --no-index --no-deps --no-compile --target /opt/coremind-sdk /tmp/coremind_ai-0.7.1-py3-none-any.whl \
    && rm /tmp/coremind_ai-0.7.1-py3-none-any.whl

ENV PYTHONPATH=/opt/coremind-sdk
WORKDIR /workspace/work
ENTRYPOINT []
CMD ["node", "/opt/coremind-sdk/coremind/_worker/coremind-worker.mjs"]
