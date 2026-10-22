#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Source-only optional helper build; does not install, invoke or change images.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output executable required (normally port/xnu/out/...)}
mkdir -p "$(dirname "$out")"
ios_sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos clang -isysroot "$ios_sdk" -target arm64-apple-ios16.0 -mcpu=cortex-a35 \
    -std=c11 -O2 -Wall -Wextra -Werror \
    "$src/a04-root-tweaks.c" -framework CoreFoundation -o "$out"
codesign --force --sign - --timestamp=none "$out"
shasum -a 256 "$src/a04-root-tweaks.c" "$src/build-a04-root-tweaks.sh" "$out" > "$out.sha256"
