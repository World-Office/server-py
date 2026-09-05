#!/usr/bin/env bash
# WOPI end-to-end validation against a running opencloud-docserver.
# Exercises the full WOPI host surface + DOCX<->HTML roundtrip.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -x "$SCRIPT_DIR/../.venv/bin/python" ]; then PY="$SCRIPT_DIR/../.venv/bin/python"; elif command -v uv >/dev/null 2>&1; then PY="uv run python"; else PY="python3"; fi
BASE="${BASE:-http://localhost:8000}"
WORK="$(mktemp -d)"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "== Seed a sample DOCX (python-docx) =="
$PY - "$WORK/sample.docx" <<'PY'
from docx import Document
d = Document()
d.add_heading("Stoic dogcow test", level=1)
d.add_paragraph("The best editor is the one you never patch at 3 AM.")
d.save(__import__("sys").argv[1])
PY
echo "  wrote $WORK/sample.docx ($(wc -c < "$WORK/sample.docx") bytes)"

echo "== Upload via /api/upload =="
UP=$(curl -s -F "file=@$WORK/sample.docx" "$BASE/api/upload")
echo "  $UP"
DOC_ID=$(printf '%s' "$UP" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "  DOC_ID=$DOC_ID"
[ -n "$DOC_ID" ] && ok "upload returned id" || bad "upload returned id"

echo "== CheckFileInfo (GET /wopi/files/{id}) =="
CFI=$(curl -s "$BASE/wopi/files/$DOC_ID")
echo "  $CFI"
printf '%s' "$CFI" | $PY -c "import sys,json;d=json.load(sys.stdin);sys.exit(0 if d.get('BaseFileName') else 1)" \
  && ok "CheckFileInfo has BaseFileName" || bad "CheckFileInfo BaseFileName"

echo "== GetFile (GET /wopi/files/{id}/contents) =="
curl -s "$BASE/wopi/files/$DOC_ID/contents" -o "$WORK/getfile.docx"
cmp -s "$WORK/sample.docx" "$WORK/getfile.docx" && ok "GetFile bytes match upload" || bad "GetFile bytes match upload"

echo "== DOCX -> HTML (GET /api/documents/{id}/html) =="
HTML=$(curl -s "$BASE/api/documents/$DOC_ID/html")
printf '%s' "$HTML" | $PY -c "import sys,json;print(json.load(sys.stdin).get('html','')[:120])"
printf '%s' "$HTML" | $PY -c "import sys,json;h=json.load(sys.stdin).get('html','');sys.exit(0 if 'dogcow' in h else 1)" \
  && ok "DOCX->HTML preserves text" || bad "DOCX->HTML preserves text"

echo "== HTML -> DOCX roundtrip (POST /api/documents/{id}/save) =="
EDITED=$(printf '%s' "$HTML" | $PY -c "import sys,json;print(json.load(sys.stdin)['html'])")
curl -s -X POST "$BASE/api/documents/$DOC_ID/save" -H "Content-Type: application/json" \
  --data "$($PY -c "import json,sys;print(json.dumps({'html':sys.argv[1]}))" "$EDITED")" -o /dev/null -w "  http=%{http_code}\n"
curl -s "$BASE/wopi/files/$DOC_ID/contents" -o "$WORK/roundtrip.docx"
$PY - "$WORK/roundtrip.docx" <<'PY' && ok "roundtrip DOCX re-parses with python-docx" || bad "roundtrip DOCX re-parses"
from docx import Document
import sys
d = Document(sys.argv[1])
txt = "\n".join(p.text for p in d.paragraphs)
print("  reparsed text:", txt[:80])
sys.exit(0 if "dogcow" in txt else 1)
PY

echo "== Lock (POST /wopi/files/{id}/lock) =="
LOCK="lock-$(date +%s)"
curl -s -o /dev/null -w "  http=%{http_code}\n" -X POST "$BASE/wopi/files/$DOC_ID/lock" -H "X-WOPI-Lock: $LOCK"

echo "== GetLock (POST /wopi/files/{id}/getlock) =="
GL_HDR=$(curl -s -D - -o /dev/null -X POST "$BASE/wopi/files/$DOC_ID/getlock" | grep -i 'X-WOPI-Lock')
echo "  $GL_HDR"
printf '%s' "$GL_HDR" | grep -qi "$LOCK" && ok "GetLock returns our lock token" || bad "GetLock returns our lock token"

echo "== PutFile with correct lock (POST /wopi/files/{id}/contents) =="
printf 'edited content marker' | curl -s -X POST "$BASE/wopi/files/$DOC_ID/contents" \
  -H "X-WOPI-Lock: $LOCK" --data-binary @- -o /dev/null -w "  http=%{http_code}\n"

echo "== PutFile with WRONG lock should be rejected =="
curl -s -o /dev/null -w "  http=%{http_code} (expect 409)\n" -X POST "$BASE/wopi/files/$DOC_ID/contents" \
  -H "X-WOPI-Lock: wrong-lock" --data-binary 'x'

echo "== Unlock (POST /wopi/files/{id}/unlock) =="
curl -s -o /dev/null -w "  http=%{http_code}\n" -X POST "$BASE/wopi/files/$DOC_ID/unlock" -H "X-WOPI-Lock: $LOCK"

echo
echo "== RESULT: PASS=$PASS FAIL=$FAIL =="
rm -rf "$WORK"
[ "$FAIL" -eq 0 ]
