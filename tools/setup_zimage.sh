#!/usr/bin/env bash
set -euo pipefail
cache=$1
mkdir -p "$cache"
cache=$(cd "$cache" && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
git clone --depth 1 --branch master-1133-52491bd --recurse-submodules --shallow-submodules \
  https://github.com/leejet/stable-diffusion.cpp.git "$stage/src"
cmake -S "$stage/src" -B "$stage/build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
cmake --build "$stage/build" --target sd-cli --parallel 2
mkdir -p "$cache/bin"
install -m 0755 "$stage/build/bin/sd-cli" "$cache/bin/sd-cli"
download() {
  curl -fLsS --retry 3 "$1" -o "$cache/$2.part"
  mv "$cache/$2.part" "$cache/$2"
}
download 'https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/c61c0e422dc8b541b7548cf33a4ef8302b0f8085/z_image_turbo-Q3_K.gguf' zimage.gguf
download 'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf' encoder.gguf
download 'https://huggingface.co/Comfy-Org/z_image_turbo/resolve/6fc90a3b1b653e935a0d175e260736de25b84df5/split_files/vae/ae.safetensors' vae.safetensors
"$cache/bin/sd-cli" --help >/dev/null
touch "$cache/.ready"
