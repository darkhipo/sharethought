#!/bin/sh
# sharethought recipe tests — pure POSIX sh, no renderer needed (fixture PNG via base64).
# Runs the SKILL.md pack/read recipes under LC_ALL=C and LC_ALL=C.utf8.
# Env: AWK (default: awk) — CI also runs AWK=gawk to pin locale-sensitive %c behavior.
set -u
AWK=${AWK:-awk}
total=0; failed=0

ok()  { total=$((total+1)); printf '  ok %d - %s\n' "$total" "$1"; }
bad() { total=$((total+1)); failed=$((failed+1)); printf '  FAIL %d - %s\n' "$total" "$1"; }
check() { # $1 = description, $2 = expected, $3 = actual
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected [$2] got [$3])"; fi
}

run_suite() {
  work=$(mktemp -d)
  printf '%s' 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC' \
    | base64 -d > "$work/base.png"
  doc_b64=$(printf '# Doc\n\nSHARETHOUGHT-v1\nquoted: iTXtsharethought and IEND\n' | base64 | tr -d '\n')
  cat > "$work/payload.txt" <<EOF
SHARETHOUGHT-v1
{"v":1,"what":"harness: mentions SHARETHOUGHT-v1 and IEND inline","how":{"unpack":"Find the ancillary text chunk whose 4 type bytes are immediately followed by the 12-byte keyword sharethought; declared length precedes the type.","repack_text_only":"rebuild both carriers","repack_visual":"re-render and repack"},"sections":[{"name":"doc","type":"text/markdown","enc":"base64","summary":"adversarial"}]}
<<<SECTION:doc>>>
$doc_b64
<<<END:doc>>>
EOF

  # --- pack recipe (SKILL.md) ---
  size=$(wc -c < "$work/base.png" | tr -d ' ')
  { printf 'sharethought\000\000\000\000\000'; cat "$work/payload.txt"; } > "$work/itxt_data"
  n=$(wc -c < "$work/itxt_data" | tr -d ' ')
  {
    LC_ALL=C head -c $((size-12)) "$work/base.png"
    LC_ALL=C $AWK -v n=$n 'BEGIN{printf "%c%c%c%c", int(n/16777216)%256, int(n/65536)%256, int(n/256)%256, n%256}'
    printf 'iTXt'
    cat "$work/itxt_data"
    { printf 'iTXt'; cat "$work/itxt_data"; } | gzip | tail -c 8 | head -c 4 | od -An -tu1 \
      | LC_ALL=C $AWK '{printf "%c%c%c%c", $4, $3, $2, $1}'
    LC_ALL=C tail -c 12 "$work/base.png"
    cat "$work/payload.txt"
  } > "$work/art.png"

  # --- 1: chunk length field bytes exact (arithmetic expectation, non-circular) ---
  h=$(LC_ALL=C grep -abo 'iTXtsharethought' "$work/art.png" | head -1 | cut -d: -f1)
  expected=$(printf '%02x %02x %02x %02x' $((n/16777216%256)) $((n/65536%256)) $((n/256%256)) $((n%256)))
  actual=$(od -An -tx1 -j $((h-4)) -N 4 "$work/art.png" | tr -s ' ' | sed 's/^ *//;s/ *$//')
  check "chunk length field bytes" "$expected" "$actual"

  len=$(LC_ALL=C od -An -tu1 -j $((h-4)) -N 4 "$work/art.png" | LC_ALL=C $AWK 'NF>3{print $1*16777216 + $2*65536 + $3*256 + $4}')

  # --- 2: IEND constant immediately after the chunk (catches CRC expansion) ---
  iend_expected='00 00 00 00 49 45 4e 44 ae 42 60 82'
  actual=$(od -An -tx1 -j $((h-4+12+len)) -N 12 "$work/art.png" | tr -s ' ' | sed 's/^ *//;s/ *$//')
  check "IEND constant after chunk (length geometry)" "$iend_expected" "$actual"

  # --- 3: IEND ends exactly at the trailer marker offset ---
  off=$(LC_ALL=C grep -abo 'SHARETHOUGHT-v1$' "$work/art.png" | tail -1 | cut -d: -f1)
  actual=$(od -An -tx1 -j $((off-12)) -N 12 "$work/art.png" | tr -s ' ' | sed 's/^ *//;s/ *$//')
  check "IEND ends at trailer marker (trailer geometry)" "$iend_expected" "$actual"

  # --- 4: chunk payload by declared length == payload ---
  if LC_ALL=C tail -c +$((h+22)) "$work/art.png" | head -c $((len-17)) | cmp -s - "$work/payload.txt"
  then ok "chunk payload exact (declared length)"; else bad "chunk payload exact"; fi

  # --- 5: stripped transport (post-IEND bytes dropped) still reads via chunk ---
  head -c $((off)) "$work/art.png" > "$work/stripped.png"
  h2=$(LC_ALL=C grep -abo 'iTXtsharethought' "$work/stripped.png" | head -1 | cut -d: -f1)
  len2=$(LC_ALL=C od -An -tu1 -j $((h2-4)) -N 4 "$work/stripped.png" | LC_ALL=C $AWK 'NF>3{print $1*16777216 + $2*65536 + $3*256 + $4}')
  if LC_ALL=C tail -c +$((h2+22)) "$work/stripped.png" | head -c $((len2-17)) | cmp -s - "$work/payload.txt"
  then ok "stripped transport: chunk read exact"; else bad "stripped transport read"; fi

  # --- 6: trailer fallback ($-anchored, to EOF) == payload ---
  if LC_ALL=C tail -c +$((off+1)) "$work/art.png" | cmp -s - "$work/payload.txt"
  then ok "trailer read exact"; else bad "trailer read"; fi

  # --- 7: base fixture sanity: last 4 bytes are IEND's CRC ---
  actual=$(od -An -tx1 -j $((size-4)) -N 4 "$work/base.png" | tr -s ' ' | sed 's/^ *//;s/ *$//')
  check "base fixture ends with IEND CRC" 'ae 42 60 82' "$actual"

  rm -rf "$work"
}

for LOCALE in C C.utf8; do
  printf '%s (awk=%s):\n' "$LOCALE" "$AWK"
  export LC_ALL=$LOCALE
  run_suite
done
printf '%d checks, %d failures\n' "$total" "$failed"
[ "$failed" -eq 0 ]
