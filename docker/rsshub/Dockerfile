# 固定原 RSSHub 版本，仅补齐它已声明的浏览器运行环境。
FROM diygod/rsshub@sha256:1fdbb65fe804e09dbc98902583ddba6f71b0ebb9a7cb51603584583672c4c503 AS browser-deps
USER root
ARG DEBIAN_MIRROR=http://deb.debian.org
# 发行镜像裁剪了 CLI；同版本安装器只用于构建，不改应用已有依赖。
RUN cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources \
    && sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && cd /tmp && npx --yes --package=patchright@1.61.1 patchright install-deps chromium \
    && cp /tmp/debian.sources /etc/apt/sources.list.d/debian.sources

FROM browser-deps
# 分层保留系统依赖，下载重试无需再次安装。
RUN cd /tmp && npx --yes --package=patchright@1.61.1 patchright install --only-shell chromium
COPY patch-baidu.mjs /tmp/patch-baidu.mjs
RUN node /tmp/patch-baidu.mjs && rm /tmp/patch-baidu.mjs
