#!/bin/bash
# Regenerate JobHunterDashboard.icns from JobHunterDashboard.png.
#
# The PNG must be a 1024x1024 full-bleed opaque tile: macOS masks app icons into
# its own rounded-square shape, so a transparent glyph gets auto-plated onto a
# dark background instead (which hides dark artwork).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-${SCRIPT_DIR}/JobHunterDashboard.png}"
OUT="${2:-${SCRIPT_DIR}/JobHunterDashboard.icns}"

if [[ ! -f "${SRC}" ]]; then
  echo "missing source image: ${SRC}" >&2
  exit 1
fi

ICONSET="$(/usr/bin/mktemp -d)/JobHunterDashboard.iconset"
/bin/mkdir -p "${ICONSET}"
trap '/bin/rm -rf "$(dirname "${ICONSET}")"' EXIT

for base in 16 32 128 256 512; do
  /usr/bin/sips -z "${base}" "${base}" "${SRC}" --out "${ICONSET}/icon_${base}x${base}.png" >/dev/null
  /usr/bin/sips -z "$((base * 2))" "$((base * 2))" "${SRC}" \
    --out "${ICONSET}/icon_${base}x${base}@2x.png" >/dev/null
done

/usr/bin/iconutil -c icns "${ICONSET}" -o "${OUT}"
echo "wrote ${OUT}"
