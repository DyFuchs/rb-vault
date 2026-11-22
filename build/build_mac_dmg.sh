#!/bin/bash
set -euo pipefail

VERSION="${1:-0.3.2}"
ARCH_LABEL="${2:-Intel}"
APP_PATH="dist/Rekordbox Vault.app"
OUT_DIR="dist_installers"
DMG_NAME="RekordboxVault-v${VERSION}-macOS-${ARCH_LABEL}.dmg"
STAGE_DMG="build/dmg_stage"

if [ ! -d "$APP_PATH" ]; then
    echo "ERRO: $APP_PATH nao encontrado!"
    exit 1
fi

rm -rf "$STAGE_DMG" "$OUT_DIR/$DMG_NAME"
mkdir -p "$STAGE_DMG" "$OUT_DIR"

cp -R "$APP_PATH" "$STAGE_DMG/"
ln -s /Applications "$STAGE_DMG/Applications"

hdiutil create \
    -volname "Rekordbox Vault v${VERSION}" \
    -srcfolder "$STAGE_DMG" \
    -ov \
    -format UDZO \
    "$OUT_DIR/$DMG_NAME"

rm -rf "$STAGE_DMG"
echo "DMG gerado com sucesso: $OUT_DIR/$DMG_NAME"
