FROM golang@sha256:622e56dbc11a8cfe87cafa2331e9a201877271cbff918af53d3be315f3da88cc AS builder

ARG SMOKESCREEN_COMMIT=da4840c9d8730fe74775573adb0b947ffe14732d
ARG SMOKESCREEN_ARCHIVE_SHA256=f4786cbb1f5651cc914e89dd114813fd013eba9d3fc780c8eef7a287391e0b09

# Smokescreen 没有官方镜像，因此只从固定官方提交构建；哈希不匹配时立即失败，
# 不使用来源不明的第三方二进制替代。
RUN wget -O /tmp/smokescreen.tar.gz \
        "https://github.com/stripe/smokescreen/archive/${SMOKESCREEN_COMMIT}.tar.gz" \
    && echo "${SMOKESCREEN_ARCHIVE_SHA256}  /tmp/smokescreen.tar.gz" \
        | sha256sum -c - \
    && mkdir -p /src /out \
    && tar -xzf /tmp/smokescreen.tar.gz -C /src --strip-components=1 \
    && cd /src \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/smokescreen .

FROM alpine@sha256:25109184c71bdad752c8312a8623239686a9a2071e8825f20acb8f2198c3f659

RUN apk add --no-cache ca-certificates
COPY --from=builder /out/smokescreen /usr/local/bin/smokescreen

USER 65532:65532
EXPOSE 4750
ENTRYPOINT ["/usr/local/bin/smokescreen"]
