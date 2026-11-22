FROM ghcr.io/openatv/enigma2-testenv/enigma2-testenv@sha256:476eeb8327fc4ed46ed338c0ba5e368e77eeabd81446a4ef95a76a916be9bafb

ARG ENIGMA2_REV=d31cb9aad315470e39bf04edcd1227279648a404
ARG BUILD_JOBS=8
LABEL org.opencontainers.image.version="8.0" \
      org.opencontainers.image.revision="${ENIGMA2_REV}" \
      org.opencontainers.image.source="https://github.com/openatv/enigma2/tree/8.0"

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libharfbuzz-dev \
      novnc websockify imagemagick x11-utils xauth \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /work/enigma2-8.0
RUN git init \
 && git fetch --depth=1 https://github.com/openatv/enigma2.git "${ENIGMA2_REV}" \
 && git checkout --detach FETCH_HEAD \
 && cp /work/enigma2/m4/ax_python_devel.m4 m4/ax_python_devel.m4 \
 && ./autogen.sh \
 && ./configure --with-libsdl --with-gstversion=1.0 --prefix=/usr --sysconfdir=/etc --with-boxtype=dm920

RUN make -j"${BUILD_JOBS}"

# Install in a staging directory so upstream install hooks can create their symlinks.
RUN make install DESTDIR=/tmp/enigma2-root \
 && mv /usr/lib/enigma2/python /tmp/enigma2-base-python \
 && cp -a /tmp/enigma2-root/. / \
 && cp /tmp/enigma2-base-python/Tools/Weatherinfo.py /usr/lib/enigma2/python/Tools/ \
 && cp -a /tmp/enigma2-base-python/Plugins/Extensions/MyMetrixLite /usr/lib/enigma2/python/Plugins/Extensions/ \
 && rm -rf /tmp/enigma2-base-python /tmp/enigma2-root \
 && ldconfig \
 && sed -i -E "s/^(imageversion|imgversion)=.*/\1='8.0'/" /usr/lib/enigma.info \
 && localedef -i de_DE -f UTF-8 de_DE.UTF-8 \
 && printf '\n%s\n' 'config.misc.locale=en_US' 'config.misc.language=en' \
      'config.misc.country=US' 'config.usage.setup_level=expert' \
      'config.usage.pluginListLayout=0' >> /etc/enigma2/settings

COPY tools/start-enigma2 /usr/local/bin/start-enigma2
ENV DISPLAY=:99 SDL_VIDEODRIVER=x11 LIBGL_ALWAYS_SOFTWARE=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /src
CMD ["/bin/bash", "/usr/local/bin/start-enigma2"]
