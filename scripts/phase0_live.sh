#!/usr/bin/env bash
# Phase 0 live validation against the real watch. Run by the OWNER, never by Claude.
# Steps (each paused, skippable): scan, pair, info, listen (30 s), notify, install ONE photo face.
# Logs (-vv, secrets redacted) go to research/logs/phase0-<timestamp>/.
#
# Secrets: cmfkit stores its auth key in plaintext at $XDG_CONFIG_HOME/cmfkit/devices.json.
# We point XDG_CONFIG_HOME at a private temp dir for this run only, move the key into the
# macOS Keychain (service "cmf-studio", account "pairing:<device uuid>") and delete the dir on exit.
set -uo pipefail
cd "$(dirname "$0")/.."

CMF="refs/CMF-Open/.venv/bin/cmfkit"
FACE_PNG="scripts/assets/test_face.png"
KC_SERVICE="cmf-studio"
LOG="research/logs/phase0-$(date +%Y%m%d-%H%M%S)"
LISTEN_SECS=30
SIM=0
# --sim: run the same steps against cmfkit's simulated watch (tests this script; no Bluetooth)
[ "${1:-}" = --sim ] && { SIM=1; LOG="$LOG-sim"; }

[ -x "$CMF" ] || { echo "cmfkit venv missing: (cd refs/CMF-Open && uv sync --frozen)"; exit 1; }
[ -f "$FACE_PNG" ] || { echo "missing $FACE_PNG"; exit 1; }
mkdir -p "$LOG"

XDG_CONFIG_HOME="$(mktemp -d)"; export XDG_CONFIG_HOME
chmod 700 "$XDG_CONFIG_HOME"
KEYFILE="$XDG_CONFIG_HOME/cmfkit/devices.json"
trap 'rm -rf "$XDG_CONFIG_HOME"' EXIT

# Strip the AT GETSECRET reply (text or hex form) and printed auth keys from everything we log.
redact() {
  sed -l -E \
    -e 's/(GETSECRET:)[0-9A-Fa-f]{32}/\1<redacted>/g' \
    -e 's/(4745545345435245543a)[0-9a-fA-F]{64}/\1<redacted>/g' \
    -e 's/([Aa]uth key:? )[0-9a-fA-F]{32}/\1<redacted>/g'
}

kc() {  # kc get|put <address>  (reads/writes $KEYFILE; key never passes through argv)
  uv run -q --no-project --with keyring python -I - "$@" "$KEYFILE" "$KC_SERVICE" <<'EOF'
import json, os, sys, keyring
op, addr, keyfile, service = sys.argv[1:5]
acct = f"pairing:{addr}"
if op == "put":
    keyring.set_password(service, acct, json.load(open(keyfile))[addr])
    print(f"auth key stored in Keychain ({service} / {acct})")
else:
    key = keyring.get_password(service, acct)
    if not key:
        sys.exit(1)
    os.makedirs(os.path.dirname(keyfile), mode=0o700, exist_ok=True)
    with open(os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({addr: key}, f)
    print("loaded existing auth key from Keychain")
EOF
}

SUMMARY=()
N=0
ask() {  # ask <step name> -> 0 run, 1 skip; q quits
  echo
  read -r -p "==> Next: $1   [Enter]=run  s=skip  q=quit: " ans
  case "$ans" in q|Q) finish; exit 0 ;; s|S) SUMMARY+=("$1: skipped"); return 1 ;; esac
  return 0
}
record() {  # record <step name> <rc>
  if [ "$2" -eq 0 ]; then SUMMARY+=("$1: ok"); else SUMMARY+=("$1: FAILED (rc=$2)"); fi
}
step() {  # step <name> <cmd...>
  local name=$1; shift
  N=$((N + 1))
  ask "$name" || return 0
  "$@" 2>&1 | redact | tee "$LOG/$(printf %02d $N)-$name.log"
  record "$name" "${PIPESTATUS[0]}"
}
finish() {
  echo; echo "Summary:" | tee "$LOG/summary.txt"
  for s in "${SUMMARY[@]+"${SUMMARY[@]}"}"; do echo "  $s" | tee -a "$LOG/summary.txt"; done
  cat <<EOF

Logs: $LOG
Manual fallback check (Phase 0 AC): turn iPhone Bluetooth back on, open Nothing X and confirm the
watch reconnects (re-pair if asked). Note the result when you paste these logs back.
EOF
}

cat <<'EOF'
CMF Studio — Phase 0 live validation
Before starting:
  * iPhone Bluetooth OFF (the watch accepts one connection at a time)
  * this terminal app (Terminal / iTerm / VS Code) allowed under
    System Settings > Privacy & Security > Bluetooth
  * watch awake and nearby
This script installs ONE photo face (Tier 1, reversible: delete it in Nothing X).
It sends no AT command except AT GETSECRET during pairing, and no firmware.
EOF
read -r -p "Ready? [y/N] " go; [ "$go" = y ] || exit 0

if [ "$SIM" = 1 ]; then
  ADDR=SIM; N=1; C=("$CMF" -vv --sim); LISTEN_SECS=3
else
# 1. scan
N=$((N + 1))
ask scan || { echo "scan is required to find the device UUID"; exit 1; }
"$CMF" -vv scan --timeout 10 2>&1 | redact | tee "$LOG/01-scan.log"
record scan "${PIPESTATUS[0]}"
CANDIDATES=$(grep -E '^[0-9A-Fa-f-]{36}  CMF Watch' "$LOG/01-scan.log" || true)
[ -n "$CANDIDATES" ] || { echo "No CMF watch found. Is Nothing X still connected?"; finish; exit 1; }
echo "$CANDIDATES" | nl
COUNT=$(echo "$CANDIDATES" | wc -l | tr -d ' ')
PICK=1
[ "$COUNT" -gt 1 ] && read -r -p "Which one? [1-$COUNT] " PICK
ADDR=$(echo "$CANDIDATES" | sed -n "${PICK}p" | awk '{print $1}')
echo "Using $ADDR" | tee -a "$LOG/01-scan.log"
kc get "$ADDR" 2>/dev/null || echo "no stored auth key; pairing step will create one"

C=("$CMF" -vv --address "$ADDR")
fi

# 2. pair (reads AT GETSECRET, then the watch shows a prompt: ACCEPT IT ON THE WATCH within 60 s)
echo; echo "Pair: when the watch shows a pairing prompt, accept it on the watch."
step pair "${C[@]}" pair
[ -f "$KEYFILE" ] && kc put "$ADDR" | tee -a "$LOG/02-pair.log"

# 3. info
step info "${C[@]}" info

# 4. listen (press each watch button / media control while it runs)
N=$((N + 1))
if ask listen; then
  echo "Listening ${LISTEN_SECS}s: open the music screen and press play/pause, next, prev, volume, find-phone."
  set -m
  "${C[@]}" listen > >(redact | tee "$LOG/$(printf %02d $N)-listen.log") 2>&1 &
  pid=$!
  sleep "$LISTEN_SECS"
  kill -INT "$pid" 2>/dev/null; sleep 3; kill -TERM "$pid" 2>/dev/null
  wait "$pid"; rc=$?
  set +m
  # 130 = interrupted by us, which is the expected way to end listen
  [ "$rc" -eq 130 ] && rc=0
  record listen "$rc"
fi

# 5. notify
step notify "${C[@]}" notify "CMF Studio" "hello"

# 6. one photo face: build offline (with decoded preview), then install the exact .bin
step face-build "$CMF" watchface build "$FACE_PNG" -o "$LOG/test_face.bin" --preview "$LOG/test_face_preview.png"
if [ -f "$LOG/test_face.bin" ]; then
  step face-install "${C[@]}" watchface install "$LOG/test_face.bin"
fi

finish
