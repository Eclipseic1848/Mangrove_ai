FROM python:3.13-slim-bookworm
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-impress fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
ENV HOME=/tmp
USER 65534:65534
ENTRYPOINT ["libreoffice", "-env:UserInstallation=file:///tmp/office-profile", "--headless", "--convert-to", "pdf", "--outdir", "/output"]
