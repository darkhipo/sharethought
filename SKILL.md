---
name: sharethought
description: Pack layered data into a PNG using only POSIX shell and standard utilities (grep, od, gzip, base64, printf, tail, head). The image humans see is the rendered surface; the SVG source and a markdown document ride inside the file — the same payload bytes in a greppable trailer after IEND and in an iTXt chunk that survives image pipelines. Use when creating, unpacking, editing, or repacking sharethought files — self-contained artifacts for human-LLM collaborative exchange.
---

# sharethought

A sharethought file is a valid PNG. It opens and renders as a normal image everywhere. Carried inside it: the SVG source the image was rendered from, a markdown document, and a manifest that indexes both and carries its own unpack/repack instructions.

One file. Two audiences. The human reads the picture; the LLM reads the depth. Both edit the same artifact.

No dependencies. Everything below runs on a basic shell plus standard utilities. You are the parser — the shell only moves bytes.

## The payload

The payload is text, identical in both containers:

```
SHARETHOUGHT-v1
<JSON manifest — one object, see below>
<<<SECTION:svg>>>
<svg source text>
<<<END:svg>>>
<<<SECTION:doc>>>
<markdown text>
<<<END:doc>>>
```

Rules, exactly:

- The first line is the marker `SHARETHOUGHT-v1`.
- The manifest is one JSON object, ending at the first `<<<SECTION` line.
- Each section body sits between `<<<SECTION:name>>>` and `<<<END:name>>>`, each on its own line.
- Section names match `[A-Za-z0-9_]+`. Any other name is an error.
- A body containing a line equal to its own `<<<END:name>>>`, or the marker string, must be carried as `"enc":"base64"` — one single-line base64 body, no newlines.
- Bodies must be NUL-free UTF-8 text. Binary data goes in base64'd sections.

### Manifest schema

```json
{
  "v": 1,
  "what": "one line describing this artifact",
  "how": {
    "unpack": "Find the ancillary text chunk whose 4 type bytes are immediately followed by the 12-byte keyword sharethought; its declared data length is the 4 big-endian bytes before the type; the payload is the final length-minus-17 bytes of the chunk data. No such chunk: search end-of-line for the marker line and print from that offset to end-of-file. Then read this manifest and each section between its <<<SECTION:name>>> and <<<END:name>>> lines; decode base64 sections.",
    "repack_text_only": "Edit section text, rebuild the payload, rewrite BOTH containers: a fresh carrier chunk before IEND and a fresh trailer after it. The base PNG never changes.",
    "repack_visual": "Edit the svg section, render it to PNG with whatever renderer exists (rsvg-convert -w 1200, cairosvg, resvg, a headless browser), then pack again from the new PNG."
  },
  "sections": [
    { "name": "svg", "type": "image/svg+xml", "enc": "utf8", "summary": "one-line description of the image" },
    { "name": "doc", "type": "text/markdown", "enc": "utf8", "summary": "one-line description of the document" }
  ]
}
```

The `how` field is mandatory and must contain working instructions — the file teaches any cold reader to open it. Do not write the concatenated framing literal (the chunk type bytes glued to the keyword) into `how`; it would false-hit the carrier search. Extra sections of any text type are allowed; list each in `sections`.

## The two containers

**A. Trailer — the payload appended after the PNG's IEND chunk.**

- Trivial to write: `cat payload.txt >> file.png`.
- Survives byte-exact transports only: git, zip, `curl -O`, a file copy.
- Does not survive anything that re-serialises the image.

**B. iTXt chunk — the payload inside the PNG chunk stream, before IEND.**

- One ancillary `iTXt` chunk, keyword `sharethought`, compression flag `0` (uncompressed — readable without zlib).
- Survives pipelines that preserve ancillary chunks (chunk-level re-writes usually do; a plain Pillow re-save does not).
- The chunk payload is **byte-identical to the trailer**. One parser for both; if both are present and differ, that is tampering — say so.

Write both when you can (the pack recipe does). Cost: roughly one extra copy of the payload.

## Read (shell)

Two recipes, in order of preference. The chunk carrier works even when the trailer was stripped in transit.

**1. Chunk carrier:**

```sh
h=$(LC_ALL=C grep -abo 'iTXtsharethought' file.png | head -1 | cut -d: -f1)
```

The 16-byte run — type bytes immediately followed by the keyword — is framing; prose never contains it, and the framing precedes the payload it wraps, so the *first* hit is the chunk. Validate it:

```sh
LC_ALL=C od -An -tx1 -j $((h+16)) -N 5 file.png    # 5 bytes after the keyword: must be 00 00 00 00 00
```

Read the chunk's **declared length** — the 4 big-endian bytes just before the type field — and take exactly that much payload, no arbitrary caps:

```sh
len=$(set -- $(LC_ALL=C od -An -tu1 -j $((h-4)) -N 4 file.png); echo $(($1*16777216 + $2*65536 + $3*256 + $4)))
LC_ALL=C tail -c +$((h+22)) file.png | head -c $((len-17))    # keyword(12) + NULs(5) = 17 bytes of framing
```

Layout: `h` points at the `iTXt` type; length field at `h-4`; chunk data — keyword, 5 NULs, payload — starts at `h+4`; payload runs `(len-17)` bytes from `h+21`. If the length is absurd or the payload does not parse, that hit was a quoted copy inside a section body; keep searching or conclude there is none.

**2. Trailer (fallback):**

```sh
off=$(LC_ALL=C grep -abo 'SHARETHOUGHT-v1$' file.png | tail -1 | cut -d: -f1)
LC_ALL=C tail -c +$((off+1)) file.png      # the payload as text, to EOF
```

The end-of-line anchor is the rule: the structural marker is always followed by a newline, while a mention inside the manifest never can be — JSON strings contain no raw newlines, so a mention always has at least its closing quote after it. Every `$`-anchored hit is therefore a payload copy — the chunk-internal one and/or the trailer — and the *last* one runs to EOF. If several hits exist they must be identical; compare them (`cmp`) and treat any difference as corruption.

Then parse: read the manifest JSON, take each section between its frame lines, `base64 -d` any `"enc":"base64"` bodies (`printf '%s' "$body" | base64 -d`).

Sanity checks: PNG signature is `89 50 4e 47 0d 0a 1a 0a` (`head -c 8 file.png | od -An -tx1`); every manifest section exists as a frame and every frame is listed in the manifest; one frame per name (duplicates = error, not last-wins).

## Pack (shell)

You need: a base PNG (any existing image, or render the SVG with whatever renderer exists), the svg source, the doc.

1. Build the payload text. Write the manifest (fill in `what`, `how`, `sections`) and the frames into a file — heredocs are fine. If any line of a body equals its own `<<<END:name>>>`, or the body contains the marker string, carry it base64:

```sh
printf '%s' "$body" | base64 | tr -d '\n'     # single line, use as the framed body
```

2. Append the trailer:

```sh
cat payload.txt >> artifact.png
```

3. Splice the iTXt carrier before IEND (recommended). Do not grep for `IEND` — the manifest's own instructions mention it. Derive it from geometry: a base PNG with no trailer ends with the IEND chunk, which is always the constant 12 bytes `00 00 00 00 49 45 4E 44 AE 42 60 82`:

```sh
size=$(wc -c < base.png | tr -d ' ')
LC_ALL=C od -An -tx1 -j $((size-4)) -N 4 base.png    # must be: ae 42 60 82 (IEND's CRC — a trailer-less PNG ends with IEND)
# the IEND chunk is the last 12 bytes: [length][IEND][crc] = [size-12, size)

# CRC32 of type+data via gzip (its trailer carries the input's CRC32, little-endian).
# Chunk length covers DATA only (keyword + 5 NULs + payload); the CRC covers type + data.
{ printf 'sharethought\000\000\000\000\000'; cat payload.txt; } > /tmp/itxt_data
n=$(wc -c < /tmp/itxt_data | tr -d ' ')
{
  LC_ALL=C head -c $((size-12)) base.png                   # everything before the IEND chunk
  # 32-bit big-endian length as 4 raw bytes — POSIX printf octal escapes
  printf "\\$(printf '%03o' $((n/16777216%256)))\\$(printf '%03o' $((n/65536%256)))\\$(printf '%03o' $((n/256%256)))\\$(printf '%03o' $((n%256)))"
  printf 'iTXt'
  cat /tmp/itxt_data
  # the 4 LE CRC bytes reversed to BE, same octal-escape trick
  ( set -- $( { printf 'iTXt'; cat /tmp/itxt_data; } | gzip | tail -c 8 | head -c 4 | od -An -tu1 )
    printf "\\$(printf '%03o' "$4")\\$(printf '%03o' "$3")\\$(printf '%03o' "$2")\\$(printf '%03o' "$1")" )
  LC_ALL=C tail -c 12 base.png                             # the IEND chunk, whole
  cat payload.txt
} > artifact.png
```

Notes: the five `\000` after the keyword are the keyword NUL, compression flag 0, method 0, and the two empty-tag NULs. `printf '\NNN'` emits the raw byte with that octal value in any locale. Shell arithmetic runs on decimal digits only. The file renders as a normal PNG; `strings artifact.png | grep SHARETHOUGHT` shows the trailer.

## Repack (shell)

**Text-only edit** (doc changed, image unchanged): derive the base PNG — everything before the IEND chunk, minus any existing carrier chunk — then run the pack steps with the edited payload. To remove an old carrier chunk: find its framing with the first `grep -abo 'iTXtsharethought'` hit, read its declared length from the 4 bytes before the type (`od -An -tu1` then shell arithmetic, big-endian), and splice out `12+length` bytes starting 4 bytes before the type. The IEND chunk of a trailer-less file is its last 12 bytes; of a trailered file, the 12 bytes ending at the trailer marker offset.

**Visual edit** (svg changed): render the modified SVG to a fresh PNG and pack again from it. Never re-save the old PNG through an image library — a Pillow re-save drops both carriers.

Round-trip rule: a repacked file is a sharethought file. Same payload shape, same marker, updated manifest.

## Verify (shell)

In order; any failure is a real defect, name it:

1. Signature: `head -c 8 file.png | od -An -tx1` → `89 50 4e 47 0d 0a 1a 0a`.
2. IEND: with a trailer, the constant 12 bytes (`00 00 00 00 49 45 4E 44 AE 42 60 82`) end exactly at the trailer marker offset; without one, they are the file's last 12 bytes.
3. Trailer parses: every end-of-line marker hit is a payload copy; the last extends to EOF; all copies `cmp` identical. The next line begins `{`; frames balanced — for each name, one `<<<SECTION:name>>>` and one `<<<END:name>>>` (`grep -c`), no orphan text between the last END frame and EOF.
4. Chunk carrier (if present): framing found (`iTXtsharethought`, first hit), five NUL bytes after the keyword, payload read by declared length and parses.
5. Both carriers present: extract both payloads and `cmp` them — identical, or the file was edited inconsistently.
6. Consistency: manifest lists exactly the framed sections; names match the grammar; base64 bodies decode.
7. Optional deep check — chunk CRCs: for any chunk, `od` out its type+data bytes, `gzip` them, compare the trailing CRC to the stored one (the pack recipe's trick run backwards).

## Portability

- Required: POSIX sh with arithmetic expansion; `grep`, `od`, `gzip`, `base64`, `tail`, `head`, `cat`, `printf`, `wc`, `tr`, `sed`, `cmp`. No awk, python, or libraries.
- All multi-byte integers in the format are big-endian by spec — no host-endianness assumptions.
- Byte emission is `printf` octal escapes only (`\NNN` → the raw byte, locale-independent).
- `head -c N` is universal in practice (GNU, BSD/macOS, busybox) but not in POSIX head; substitute `dd if=file bs=1 skip=S count=N 2>/dev/null` for strict POSIX (slow for large prefixes).
- `grep -a` (treat binary as text) and `-o` (print match only) are GNU/BSD, not POSIX — present in GNU, BSD/macOS, and busybox grep. Without them, compute offsets from `grep -b` line offsets: a `$`-anchored match ends its line, so the marker offset = line offset + line byte length − marker length.
- All byte-emitting steps still run under `LC_ALL=C` as a cheap invariant; the recipes are correct under any locale.

## Keeping the payload alive

The payload is not pixel data. Anything that re-encodes the image can drop it silently and leave a normal-looking PNG with no depth — image editors, CDN optimisers, `Image.open(f).save(g)` in Pillow. Repack from original bytes, never re-save.

Trailers survive byte-exact transports. The chunk carrier survives pipelines that preserve ancillary `iTXt` chunks — no carrier survives a PNG→JPEG transcode. Send the file as a *file*, not an inline image.

## Working with a human

The human sees the PNG — the simplified, visual layer. All depth (svg source, doc, manifest) is invisible to casual viewing but fully present for you. When the human asks about the image, unpack and read the doc for context. When the human wants the picture changed, edit the svg and repack visual. When the human wants depth added, edit the doc and repack text-only. The file is the whole exchange — one artifact, both modalities, no side channels.
