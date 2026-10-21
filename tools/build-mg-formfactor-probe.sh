#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Public iPhoneOS SDK only; the installed donor dyld supplies libMobileGestalt.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output executable required}
mkdir -p "$(dirname "$out")"
ios_sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos clang -target arm64-apple-ios16.0 -isysroot "$ios_sdk" \
    -mcpu=cortex-a35 -std=c11 -O2 -Wall -Wextra -Werror \
    "$src/mg-formfactor-probe.c" -framework CoreFoundation -o "$out"
codesign --force --sign - --timestamp=none "$out"
