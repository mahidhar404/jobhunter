#!/bin/bash
# Rebuild the AppleScript app and restore its custom icon.
# CHR2-009: injects script-relative ROOT into JobHunterDashboard.applescript
# before osacompile (placeholder __JOB_HUNTER_ROOT__).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_PATH="${1:-${HOME}/Desktop/HxH.app}"
APP_DISPLAY_NAME="${JOB_HUNTER_APP_NAME:-HxH}"
APPLESCRIPT_SRC="${SCRIPT_DIR}/JobHunterDashboard.applescript"
ICON="${SCRIPT_DIR}/JobHunterDashboard.icns"
APP_ICON="${APP_PATH}/Contents/Resources/applet.icns"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
TMP_ASCRIPT="$(mktemp -t JobHunterDashboard.XXXXXX.applescript)"

cleanup() {
  rm -f "${TMP_ASCRIPT}" 2>/dev/null || true
}
trap cleanup EXIT

if [[ ! -f "${APPLESCRIPT_SRC}" ]]; then
  echo "missing AppleScript source: ${APPLESCRIPT_SRC}" >&2
  exit 1
fi
if [[ ! -f "${ICON}" ]]; then
  echo "missing dashboard icon: ${ICON}" >&2
  exit 1
fi

# Substitute repo root into the compiled applet (Desktop app lives outside repo).
/usr/bin/sed "s|__JOB_HUNTER_ROOT__|${ROOT}|g" "${APPLESCRIPT_SRC}" > "${TMP_ASCRIPT}"
if /usr/bin/grep -q '__JOB_HUNTER_ROOT__' "${TMP_ASCRIPT}"; then
  echo "error: ROOT placeholder not fully substituted" >&2
  exit 1
fi

/usr/bin/osacompile -o "${APP_PATH}" "${TMP_ASCRIPT}"
/bin/cp -f "${ICON}" "${APP_ICON}"

# osacompile ships the stock applet artwork in an asset catalog and points
# CFBundleIconName at it. That key wins over CFBundleIconFile, so applet.icns is
# ignored until both the key and the catalog are gone.
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "${APP_PATH}/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleName ${APP_DISPLAY_NAME}" "${APP_PATH}/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string ${APP_DISPLAY_NAME}" "${APP_PATH}/Contents/Info.plist"
/bin/rm -f "${APP_PATH}/Contents/Resources/Assets.car"

# Editing Resources breaks the ad-hoc seal osacompile applies.
/usr/bin/codesign --force --deep --sign - "${APP_PATH}"

/usr/bin/touch "${APP_ICON}" "${APP_PATH}/Contents/Info.plist" "${APP_PATH}/Contents" "${APP_PATH}"
"${LSREGISTER}" -f "${APP_PATH}"

echo "rebuilt ${APP_PATH}"
echo "ROOT=${ROOT}"
echo "installed icon ${APP_ICON}"
echo "run 'killall Finder' if the Desktop still shows the previous icon"
echo ""
echo "CHR3-005: three CfT processes share one Dock icon — focus by PID:"
echo "  UI:   ${SCRIPT_DIR}/launch_dashboard.sh --focus-ui"
echo "  Fill: ${SCRIPT_DIR}/launch_dashboard.sh --focus-fill"
echo "  Roles:${SCRIPT_DIR}/launch_dashboard.sh --cft-roles"
echo "  PartyRock: ${ROOT}/open_partyrock.sh (not Dock activate)"
