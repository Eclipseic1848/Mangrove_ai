FROM python@sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64 AS python-runtime

# 复用本机已有、同为 Debian 12 的成熟 Firecrawl Runtime，避免慢网速下重复拉取
# Node/Git/curl/bash。摘要固定后不会被 latest 漂移影响实际构建。
FROM ghcr.io/firecrawl/firecrawl@sha256:d538fabd8436bcbb28aa1f4d8653f71a91fdbecc0ee888111444d695cac1ff60

ARG PI_VERSION=0.80.10

USER root

# Python 官方镜像和运行镜像同为 Debian 12；只复制解释器、标准库和 pip，
# 不把 Python 镜像里的系统目录覆盖到运行环境。
COPY --from=python-runtime /usr/local/bin/python /usr/local/bin/python
COPY --from=python-runtime /usr/local/bin/python3 /usr/local/bin/python3
COPY --from=python-runtime /usr/local/bin/python3.13 /usr/local/bin/python3.13
COPY --from=python-runtime /usr/local/bin/pip /usr/local/bin/pip
COPY --from=python-runtime /usr/local/bin/pip3 /usr/local/bin/pip3
COPY --from=python-runtime /usr/local/bin/pip3.13 /usr/local/bin/pip3.13
COPY --from=python-runtime /usr/local/lib/libpython3.13.so /usr/local/lib/libpython3.13.so
COPY --from=python-runtime /usr/local/lib/libpython3.13.so.1.0 /usr/local/lib/libpython3.13.so.1.0
COPY --from=python-runtime /usr/local/lib/python3.13 /usr/local/lib/python3.13

# 首批真实 PDF/Word/Excel A/B 证明这些成熟工具已覆盖数字 PDF 表格和 Office 读取；
# 固定进镜像可避免每个任务重复下载，也不为单一业务问法自写解析器。
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
        "pdfplumber==0.11.10" \
        "pypdf==6.14.2" \
        "openpyxl==3.1.5" \
        "python-docx==1.2.0" \
        "xlrd==2.0.2" \
    && npm install --global "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && pi --version \
    && python3 --version \
    && git --version

WORKDIR /workspace/work

ENTRYPOINT []
CMD ["pi", "--mode", "rpc"]
