FROM golang:1.25.4-bookworm AS build

WORKDIR /src
COPY postgres.patch /tmp/postgres.patch
RUN git init -q \
 && git fetch --depth=1 https://github.com/GlyphLang/GlyphLang.git b6b54b6ea685da4110994bc6283681547d44e63d \
 && git checkout -q --detach FETCH_HEAD \
 && git apply /tmp/postgres.patch \
 && CGO_ENABLED=0 go build -trimpath -ldflags='-X main.version=0.8.0' -o /glyph ./cmd/glyph

FROM debian:12.11-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /glyph /usr/local/bin/glyph
COPY --from=build /src/llms.txt /opt/glyphlang/llms.txt
