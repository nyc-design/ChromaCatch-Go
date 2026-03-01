#!/usr/bin/env bash
# Download and install MediaMTX binary for the current platform.
# Usage: ./scripts/install_mediamtx.sh [version]
set -euo pipefail

VERSION="${1:-v1.11.3}"
INSTALL_DIR="$(cd "$(dirname "$0")/../services/backend/mediamtx/bin" && pwd -P 2>/dev/null || echo "$(dirname "$0")/../services/backend/mediamtx/bin")"
mkdir -p "$INSTALL_DIR"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$ARCH" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64v8" ;;
    armv7l|armhf)  ARCH="armv7" ;;
    armv6l)        ARCH="armv6" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

case "$OS" in
    linux)  PLATFORM="linux" ;;
    darwin) PLATFORM="darwin" ;;
    *) echo "Unsupported OS: $OS" >&2; exit 1 ;;
esac

TARBALL="mediamtx_${VERSION}_${PLATFORM}_${ARCH}.tar.gz"
URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/${TARBALL}"

echo "Downloading MediaMTX ${VERSION} for ${PLATFORM}/${ARCH}..."
echo "  URL: ${URL}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fSL --retry 3 -o "$TMP/$TARBALL" "$URL"
tar -xzf "$TMP/$TARBALL" -C "$TMP"
mv "$TMP/mediamtx" "$INSTALL_DIR/mediamtx"
chmod +x "$INSTALL_DIR/mediamtx"

echo "Installed: $INSTALL_DIR/mediamtx"
"$INSTALL_DIR/mediamtx" --version 2>/dev/null || "$INSTALL_DIR/mediamtx" --help 2>/dev/null | head -1 || true
echo "Done. Add $INSTALL_DIR to PATH or set CC_BACKEND_MEDIAMTX_BINARY=$INSTALL_DIR/mediamtx"
