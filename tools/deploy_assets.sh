#!/bin/sh
# Seed a new installation, but preserve the selected runtime picture and its metadata on later deploys.
set -eu
SOURCE=$1
TARGET=$2
rsync -a --ignore-existing --include '/bg.png' --include '/art.png' --include '/voice_bg.png' \
  --include '/bg.qoi' --include '/art.qoi' --include '/voice_bg.qoi' --exclude '*' \
  "${SOURCE%/}/" "${TARGET%/}/"
rsync -a --delete --exclude '/bg.png' --exclude '/art.png' --exclude '/voice_bg.png' \
  --exclude '/bg.qoi' --exclude '/art.qoi' --exclude '/voice_bg.qoi' --exclude '/apod.json' \
  "${SOURCE%/}/" "${TARGET%/}/"
