#!/usr/bin/env bash
# Verify toolchain. Does not touch the watch.
ok(){ printf "  [ok]   %s\n" "$1"; }; bad(){ printf "  [MISS] %s — %s\n" "$1" "$2"; }
echo "Toolchain:"
for t in git uv node gh claude; do command -v $t >/dev/null && ok "$t" || bad "$t" "brew install $t (claude: see Claude Code docs)"; done
uv python find 3.12 >/dev/null 2>&1 && ok "python 3.12 (uv)" || bad "python 3.12" "uv python install 3.12"
command -v npx >/dev/null && ok "npx" || bad "npx" "comes with node"
echo "Optional (Phase 6):"
[ -d /Applications/Ghidra.app ] || ls /opt/homebrew/Caskroom/ghidra >/dev/null 2>&1 && ok "ghidra" || bad "ghidra" "brew install --cask ghidra"
command -v java >/dev/null && ok "java" || bad "java" "brew install --cask temurin@21"
echo "Reference repos:"
for d in CMF-Open CMF-Watch-Pro-2-BLE-Protocol cmf-watch-firmware; do [ -d "refs/$d" ] && ok "refs/$d" || bad "refs/$d" "bash scripts/bootstrap.sh"; done
echo
echo "Bluetooth permission can't be checked from a script:"
echo "  System Settings → Privacy & Security → Bluetooth → enable your terminal app."
echo "Before any live step: turn OFF Bluetooth on your iPhone (watch allows one connection)."
