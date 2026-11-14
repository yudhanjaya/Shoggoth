#!/bin/sh
# Builds whisper-server (whisper.cpp) for Shoggoth's speech to text.
# whisper.cpp publishes Linux binaries for the processor only, so the GPU builds have to be made here (or in CI).
#
#   packaging/build-whisper.sh cpu|cuda [tag]   -> dist/whisper-<tag>-<backend>.tar.gz and a .sha256 next to it
#
# CI note: run this on an ubuntu runner (cuda: a runner with the CUDA toolkit, e.g. nvidia/cuda:12.8.0-devel-ubuntu22.04),
# upload both files to the Shoggoth release, and put the sha256 where speech.py can check it.
# The tarball has one top folder (whisper-<tag>-<backend>/) holding whisper-server and its libraries, the same layout
# whisper.cpp's own Linux archive uses, so Shoggoth's safe unpacker handles it. Unpack it into
# <data>/whisper/<tag>/<backend>/ to use it.
set -eu
backend="${1:-cpu}"
tag="${2:-v1.9.5}"
case "$backend" in
  cpu)  flags="" ;;
  cuda) flags="-DGGML_CUDA=1 -DCMAKE_CUDA_ARCHITECTURES=75-real;86-real;89-real"; export CUDAARCHS="${CUDAARCHS:-75;86;89}" ;;   # newer nvcc drops its old default arch (sm_52)
  *) echo "backend must be cpu or cuda" >&2; exit 2 ;;
esac
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
git clone --depth 1 --branch "$tag" https://github.com/ggml-org/whisper.cpp "$work/src"
# shellcheck disable=SC2086
cmake -S "$work/src" -B "$work/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON \
      -DWHISPER_BUILD_EXAMPLES=ON -DCMAKE_INSTALL_RPATH='$ORIGIN' -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON $flags
cmake --build "$work/build" -j "$(nproc)" --target whisper-server
name="whisper-$tag-$backend"
mkdir -p "$work/$name" "$here/dist"
cp "$work/build/bin/whisper-server" "$work/$name/"
find "$work/build" -name '*.so*' -exec cp -P {} "$work/$name/" \;
cp "$work/src/LICENSE" "$work/$name/"
tar -C "$work" -czf "$here/dist/$name.tar.gz" "$name"
(cd "$here/dist" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
echo "built dist/$name.tar.gz"
