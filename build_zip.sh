#!/bin/sh
# Собирает dist/strip_sheets-<версия>.zip для «Модули → Установить из ZIP».
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^version=//p' strip_sheets/metadata.txt)
mkdir -p dist
ZIP="dist/strip_sheets-$VERSION.zip"
rm -f "$ZIP"
zip -qr "$ZIP" strip_sheets -x '*__pycache__*' '*.pyc' '*.DS_Store'
echo "$ZIP"
