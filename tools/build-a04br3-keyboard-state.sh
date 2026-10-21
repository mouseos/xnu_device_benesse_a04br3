#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Build and sign only. Never install, launch, or acquire a target task.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output executable required (normally port/xnu/out/...)}
mkdir -p "$(dirname "$out")"
build_tmp=$(mktemp "${out}.build.XXXXXX")
trap 'rm -f "$build_tmp"' EXIT
ios_sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos clang -isysroot "$ios_sdk" -target arm64-apple-ios16.0 -mcpu=cortex-a35 \
    -std=c11 -O2 -Wall -Wextra -Werror \
    "$src/a04br3_keyboard_state.c" -o "$build_tmp"
codesign --force --sign - --timestamp=none "$build_tmp"
codesign --verify --strict "$build_tmp"
mv -f "$build_tmp" "$out"
shasum -a 256 "$src/a04br3_keyboard_state.c" "$src/a04br3_keyboard_state_sources.json" \
    "$src/build-a04br3-keyboard-state.sh" "$out" > "$out.sha256"
