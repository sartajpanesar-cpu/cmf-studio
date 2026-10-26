#!/usr/bin/env bash
# Clone reference repos (read-only) and pin their commits.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p refs
clone() {  # url dir
  if [ ! -d "refs/$2/.git" ]; then git clone --depth 50 "$1" "refs/$2"; else echo "refs/$2 exists, skipping"; fi
}
clone https://github.com/TechJeeper/CMF-Open.git                       CMF-Open
clone https://github.com/joshuapassos/CMF-Watch-Pro-2-BLE-Protocol.git CMF-Watch-Pro-2-BLE-Protocol
clone https://github.com/whatotter/cmf-watch-firmware.git              cmf-watch-firmware
# Large; needed in Phase 2. Uncomment then:
clone https://codeberg.org/Freeyourgadget/Gadgetbridge.git            Gadgetbridge
# Phase 6 only (large):
# clone https://github.com/lvgl/lv_port_actions_technology.git          lv_port_actions_technology

{ echo "# Pinned reference commits ($(date +%F))"; echo
  for d in refs/*/; do [ -d "$d/.git" ] && echo "- $(basename "$d"): $(git -C "$d" rev-parse HEAD)"; done
} > refs/PINS.md
echo "Pinned:"; cat refs/PINS.md
echo
echo "Next: bash scripts/check_env.sh, then start Claude Code and paste the Phase 0 prompt."
echo "Note: cmfkit setup (uv sync inside refs/CMF-Open) happens in Phase 0 after Claude Code audits it."
