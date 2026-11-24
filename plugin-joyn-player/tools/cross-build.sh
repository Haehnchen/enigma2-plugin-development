#!/usr/bin/env bash
set -euo pipefail
source_dir=$1
sdk=$2
compiler=arm-linux-gnueabihf-g++
stripper=arm-linux-gnueabihf-objcopy
if [[ -n "${JOYN_CROSS_ROOT:-}" ]]; then
    export LD_LIBRARY_PATH="${JOYN_CROSS_ROOT}/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    compiler="${JOYN_CROSS_ROOT}/usr/bin/$compiler --sysroot=${JOYN_CROSS_ROOT}"
    stripper="${JOYN_CROSS_ROOT}/usr/bin/$stripper"
fi
make -C "$source_dir" -j"${JOBS:-4}" \
    CXX="$compiler" \
    CPPFLAGS="-Isrc/addons/kodi-dev-kit/include -I$sdk/usr/include -MMD -MP -g" \
    CXXFLAGS="-march=armv7-a -mfpu=neon-vfpv4 -mfloat-abi=hard -O1" \
    LDFLAGS="-L$sdk/usr/lib -Wl,--allow-shlib-undefined -ltinyxml2 -lcurl -lavformat -lavcodec -lavutil"
"$stripper" --strip-debug "$source_dir/build/dash2ts"
