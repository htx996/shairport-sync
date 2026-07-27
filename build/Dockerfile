# syntax=docker/dockerfile:1

FROM --platform=$BUILDPLATFORM debian:bookworm-slim AS builder

ARG TARGETARCH=amd64
ARG SHAIRPORT_SYNC_REF=master
ARG NQPTP_REF=main
ARG RAOP_AM_MODEL=""

RUN set -eux; \
    if [ "$TARGETARCH" != "amd64" ]; then \
      echo "This project targets linux/amd64 for UGREEN DXP4800 Pro. Set platform: linux/amd64." >&2; \
      exit 1; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      git \
      autoconf \
      automake \
      libtool \
      pkg-config \
      ca-certificates \
      xxd \
      libpopt-dev \
      libconfig-dev \
      libasound2-dev \
      avahi-daemon \
      libavahi-client-dev \
      libssl-dev \
      libsoxr-dev \
      libplist-dev \
      libsodium-dev \
      uuid-dev \
      libgcrypt20-dev \
      libavutil-dev \
      libavcodec-dev \
      libavformat-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN set -eux; \
    git clone https://github.com/mikebrady/nqptp.git; \
    cd nqptp; \
    git checkout "$NQPTP_REF"; \
    autoreconf -fi; \
    ./configure --prefix=/usr; \
    make -j"$(nproc)"; \
    make DESTDIR=/opt/root install

RUN set -eux; \
    git clone https://github.com/mikebrady/shairport-sync.git; \
    cd shairport-sync; \
    git checkout "$SHAIRPORT_SYNC_REF"; \
    grep -n 'config.model = strdup("ShairportSync")' shairport.c || { \
      echo "SPS_MODEL patch target not found. Actual config.model lines:" >&2; \
      grep -n 'config\.model' shairport.c >&2 || true; \
      exit 1; \
    }; \
    sed -i 's|config\.model = strdup("ShairportSync");|{ const char *_sps_m = getenv("SPS_MODEL");\n  config.model = strdup((_sps_m \&\& *_sps_m) ? _sps_m : "ShairportSync"); }|' shairport.c; \
    grep -n 'getenv("SPS_MODEL")' shairport.c || { \
      echo "SPS_MODEL patch self-check failed. Actual config.model lines:" >&2; \
      grep -n 'config\.model' shairport.c >&2 || true; \
      exit 1; \
    }; \
    case "$RAOP_AM_MODEL" in \
      ""|"ShairportSync"|"AirPort10,115"|"AudioAccessory5,1"|"AudioAccessory1,2"|"AudioAccessory6,1"|"AppleTV6,2") ;; \
      *) echo "Invalid RAOP_AM_MODEL: $RAOP_AM_MODEL" >&2; exit 1 ;; \
    esac; \
    if [ -n "$RAOP_AM_MODEL" ] && [ "$RAOP_AM_MODEL" != "ShairportSync" ]; then \
      sed -i "s/am=ShairportSync/am=$RAOP_AM_MODEL/g" mdns.h; \
      grep -n "am=$RAOP_AM_MODEL" mdns.h || { \
        echo "RAOP am patch self-check failed. Actual am lines:" >&2; \
        grep -n 'am=' mdns.h >&2 || true; \
        exit 1; \
      }; \
    else \
      grep -n 'am=ShairportSync' mdns.h; \
    fi; \
    autoreconf -fi; \
    ./configure --prefix=/usr --sysconfdir=/etc \
      --with-alsa \
      --with-soxr \
      --with-avahi \
      --with-ssl=openssl \
      --with-airplay-2; \
    make -j"$(nproc)"; \
    make DESTDIR=/opt/root install

FROM --platform=linux/amd64 debian:bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      alsa-utils \
      avahi-daemon \
      dbus \
      ca-certificates \
      procps \
      libpopt0 \
      libconfig9 \
      libasound2 \
      libavahi-client3 \
      libssl3 \
      libsoxr0 \
      libplist3 \
      libsodium23 \
      uuid-runtime \
      libgcrypt20 \
      libavutil57 \
      libavcodec59 \
      libavformat59 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/root/ /
COPY build/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /config /run/dbus

VOLUME ["/config"]
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
