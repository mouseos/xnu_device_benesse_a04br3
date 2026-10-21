#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output executable required}
mkdir -p "$(dirname "$out")/uart-stubs"
cp "$src/uart-libSystem.tbd" "$(dirname "$out")/uart-stubs/libSystem.tbd"
ios_sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos clang -isysroot "$ios_sdk" -target arm64-apple-ios16.0 -mcpu=cortex-a35 -std=c11 -O2 \
    -ffreestanding -fno-stack-protector -nostdlib -Wall -Wextra -Werror \
    -Wl,-e,_main -Wl,-platform_version,ios,16.0,16.0 \
    -L"$(dirname "$out")/uart-stubs" -lSystem "$src/uart-console.c" -o "$out"
codesign --force --sign - --timestamp=none "$out"
