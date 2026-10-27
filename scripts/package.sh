#!/bin/sh
# Build the frontend and make a ZIP for Decky > Developer > Install Plugin from ZIP.
set -eu
cd "$(dirname "$0")/.."

npx -y pnpm@9 install --frozen-lockfile
npx -y pnpm@9 build

name=decky-xodus-chat
version=$(node -p 'require("./package.json").version')
rm -rf out
mkdir -p "out/$name/dist"
cp dist/index.js "out/$name/dist/"
cp main.py plugin.json package.json LICENSE README.md "out/$name/"
(cd out && zip -qr "$name-$version.zip" "$name")
echo "out/$name-$version.zip"
