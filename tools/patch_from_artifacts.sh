#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
if [[ $# -ne 4 ]]; then
  cat >&2 <<'EOF'
Usage:
  bash tools/patch_from_artifacts.sh ORIGINAL_MINECRAFT.apk libnova_core.so nova-bootstrap.dex OUTPUT_NOVA.apk

Target: one complete Minecraft Bedrock 1.26.50.4 ARM64 APK (not an .apks bundle).
EOF
  exit 2
fi
APK="$1"
CORE="$2"
DEX="$3"
OUT="$4"
for tool in python java apktool aapt2 zipalign apksigner keytool; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing required Termux tool: $tool" >&2; exit 1; }
done
[[ -f "$APK" && -f "$CORE" && -f "$DEX" ]] || { echo "Input file missing" >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python "$SCRIPT_DIR/patch_minecraft.py" --apk "$APK" --native-lib "$CORE" --bootstrap-dex "$DEX" --out "$OUT"
