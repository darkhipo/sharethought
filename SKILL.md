---
name: sharethought
description: Pack layered data into a PNG. The image humans see is the rendered SVG surface; the SVG source and a markdown document ride inside the file — in iTXt chunks (survives pipelines that preserve ancillary chunks) and in a greppable trailer after IEND. Use when creating, unpacking, editing, or repacking sharethought files — self-contained artifacts for human-LLM collaborative exchange where the human reads the picture and the LLM reads the depth.
---

# sharethought

A sharethought file is a valid PNG with text sections carried alongside the image. It opens and renders as a normal image everywhere. Behind the image: the SVG source it was rendered from, and a markdown document. A manifest at the front of the carried data indexes everything and carries the unpack/repack instructions inside the file itself.

The sections can travel in one of two carriers: appended after the IEND chunk (simple, byte-exact transports only), or inside PNG ancillary chunks (survives image pipelines). See [Carriers](#carriers).

## Use the reference implementation

`sharethought.py` in this repo is the tested, stdlib-only implementation. Prefer it over hand-rolled parsing — every re-implementation is a fresh bug surface.

```
python3 sharethought.py pack   --png base.png --svg s.svg --doc d.md -o out.png --what "one line"
python3 sharethought.py unpack out.png [--out-dir DIR]
python3 sharethought.py repack --text out.png --set doc=new.md -o out2.png
python3 sharethought.py verify out.png
python3 -m unittest -q test_sharethought    # the test suite
```

`pack` writes both carriers from one build (they cannot diverge). `unpack` prefers chunks, falls back to the trailer, and errors loudly if both carriers are present but disagree — pack cannot produce that, so disagreement means external tampering. `verify` checks signature, every chunk's CRC, both carriers, section-name grammar, duplicate sections, and manifest↔section consistency — enforced identically for every carrier.

## Format (exact)

```
[ PNG bytes ... IEND chunk ]
SHARETHOUGHT-v1\n
[ JSON manifest — one UTF-8 object, see schema ]\n
<<<SECTION:name>>>\n
[ section content — raw UTF-8 by default, base64 if "enc":"base64" ]\n
<<<END:name>>>\n
... (further sections, same framing)
```

The marker `SHARETHOUGHT-v1` begins immediately after the PNG's IEND chunk and is terminated by a newline. (The byte before it is the IEND chunk's CRC tail, not a newline — so find it by byte, not by line.) Everything from the marker to EOF is the trailer. Standard sections: `svg` (the image source, `image/svg+xml`) and `doc` (markdown, `text/markdown`). Arbitrary additional sections are allowed — any text type.

**Section names must match `[A-Za-z0-9_]+`.** Names outside that grammar are rejected at pack time and flagged with a named error at unpack — they never silently disappear.

## Carriers

The same manifest and sections can be carried two ways. Choose by transport.

**A. Trailer — appended after the IEND chunk** (the default above).

- Trivial to write by hand: `cat trailer.txt >> file.png`. No CRC, no chunk framing, no library.
- Survives **byte-exact transports only**: git, zip, `curl -O`, a direct file copy.
- Does **not** survive any pipeline that re-serialises or re-encodes the image. A PNG→JPEG transcode drops it entirely. A chunk-level re-write — which many chat and drive pipelines do — keeps every IDAT byte and discards everything after IEND.

**B. Ancillary chunks — `iTXt` inside the PNG chunk stream.**

- Lives inside the chunk list, so it survives any pipeline that preserves ancillary `iTXt` chunks — chunk-level re-writes usually do. A plain Pillow re-save does not (measured); neither does anything that re-encodes.
- Costs a CRC and chunk framing, and large sections want compressing.
- Does **not** survive a PNG→JPEG transcode — nothing survives that except the pixels.

**Recommendation: write both** (`pack` does). Same payload, one build, no divergence possible. A reader looks for chunks first and falls back to the trailer, so the file works wherever ancillary chunks survive and on byte-exact transports alike. The cost is roughly one extra copy of the payload.

Chunk layout:

```
[ IHDR ] [ IDAT ... ] [ iTXt sharethought:manifest ] [ iTXt sharethought:<name> ] ... [ IEND ]
```

One `iTXt` per section: keyword `sharethought:manifest` for the manifest, `sharethought:<name>` for each section body (raw body, no delimiters). Standard `iTXt` framing — keyword `\0`, compression flag (1 byte, `1` = zlib), compression method (`0`), language tag `\0`, translated keyword `\0`, then the text.

### Manifest schema

```json
{
  "v": 1,
  "what": "one line describing this artifact",
  "how": {
    "unpack": "Read the iTXt chunks named sharethought:* if present (the manifest is sharethought:manifest); otherwise read bytes from the SHARETHOUGHT-v1 marker to EOF. Parse the JSON manifest, then each section between its <<<SECTION:name>>> and <<<END:name>>> delimiters. Decode base64 sections.",
    "repack_text_only": "Keep the original PNG bytes up to the marker, then rewrite the trailer with updated manifest and sections. No re-render needed. Or run: python3 sharethought.py repack --text FILE --set doc=new.md",
    "repack_visual": "To change the image: edit the svg section, render it to PNG (rsvg-convert -w 1200 in.svg > out.png, or cairosvg, or resvg, or a headless browser such as Chromium via Playwright), then pack again."
  },
  "sections": [
    { "name": "svg",  "type": "image/svg+xml", "enc": "utf8",    "summary": "one-line description of the image" },
    { "name": "doc",  "type": "text/markdown", "enc": "utf8",    "summary": "one-line description of the document" }
  ]
}
```

The `how` field is mandatory and must contain working instructions — the file teaches any cold reader to open it.

## Unpack

Best: `python3 sharethought.py unpack file.png`. Manual, trailer carrier:

```python
raw = open("file.png", "rb").read()
# Walk the PNG chunks to the real IEND — never raw.index() the marker,
# compressed IDAT bytes can contain it by coincidence.
off = 8
while True:
    import struct
    ln, typ = struct.unpack(">I", raw[off:off+4])[0], raw[off+4:off+8]
    if typ == b"IEND": break
    off += 12 + ln
trailer = raw[off + 12:].decode("utf-8")
import json
manifest, _ = json.JSONDecoder().raw_decode(trailer[trailer.index("\n")+1:])
import re
for m in re.finditer(r"<<<SECTION:(\w+)>>>\n(.*?)\n<<<END:\1>>>", trailer, re.DOTALL):
    name, body = m.group(1), m.group(2)
    # if enc is base64: import base64; body = base64.b64decode(body)
```

Manual, chunk carrier (checked first):

```python
import struct, zlib, json
raw = open("file.png", "rb").read()
off, chunks = 8, {}
while off + 8 <= len(raw):
    ln = struct.unpack(">I", raw[off:off+4])[0]
    typ = raw[off+4:off+8]
    data = raw[off+8:off+8+ln]
    if typ == b"IEND": break
    if typ == b"iTXt":
        key, _, rest = data.partition(b"\x00")
        flag = rest[0]
        rest = rest[2:]
        _, _, rest = rest.partition(b"\x00")   # language tag
        _, _, rest = rest.partition(b"\x00")   # translated keyword
        if flag == 1:
            rest = zlib.decompress(rest)
        chunks[key.decode()] = rest.decode("utf-8")
    off += 12 + ln
manifest = json.loads(chunks["sharethought:manifest"])
sections = {k[len("sharethought:"):]: v
            for k, v in chunks.items()
            if k.startswith("sharethought:") and k != "sharethought:manifest"}
```

With shell only: `tail -c +$(( $(grep -abo 'SHARETHOUGHT-v1' file.png | head -1 | cut -d: -f1) + 1 )) file.png` prints the trailer as text (works only when the trailer survived transport).

## Create

Best: render the SVG, then `python3 sharethought.py pack --png out.png --svg diagram.svg --doc notes.md -o artifact.png`. Manual:

1. Write the SVG (the human-facing picture or diagram).
2. Render to PNG: `rsvg-convert -w 1200 diagram.svg > out.png` (or `cairosvg diagram.svg -o out.png`, or `resvg diagram.svg out.png`, or a headless browser — Chromium via Playwright — which is often the only SVG renderer installed).
3. Write the markdown document.
4. Append the trailer (`cat trailer.txt >> file.png` shape) and, better, also write the iTXt chunks — `pack` does both from one build.

The output renders as a normal PNG. Verify: open it in any image viewer (shows the image), then `strings artifact.png | grep SHARETHOUGHT` (shows the trailer exists), or `python3 sharethought.py verify artifact.png` (checks everything, including that both carriers agree).

## Repack

Best: `python3 sharethought.py repack --text artifact.png --set doc=new.md -o out.png` — rewrites both carriers, no renderer needed.

**Text-only edit** (doc changed, image unchanged): reuse the original PNG bytes (everything before `SHARETHOUGHT-v1`), write a new trailer with the edited section and updated manifest summaries. If the file also carries chunks, rewrite those too — the reference implementation rebuilds both from one source, which is the only safe way to keep them agreeing.

**Visual edit** (svg changed): render the modified SVG to a fresh PNG, then pack again with the new svg section + doc + manifest. Any SVG renderer works, including a headless browser.

Round-trip rule: a repacked file is a sharethought file. Same format, same marker, updated manifest.

## Delimiter safety

Section delimiters are `<<<SECTION:name>>>` and `<<<END:name>>>` on their own lines. If content could contain such a line, set `"enc":"base64"` in the manifest for that section and base64 the body — `pack` does this automatically whenever any line of a body IS its own closing delimiter (first line, middle, or last) or contains the marker string.

Which collisions break and which don't: a body containing a *different* section's delimiters is harmless (regression-tested — the reader matches by name, not by global counting). A body containing **its own** `<<<END:name>>>` line, anywhere including as the very first line, is auto-upgraded to base64 at pack. A hand-made file that still manages an unclosed section fails with a named error (orphaned content / missing section), never silent truncation. When in doubt, base64.

## Keeping the payload alive

The payload is not part of the PNG's pixel data, so any tool that re-encodes the image can drop it silently and leave a perfectly normal-looking PNG with no depth. That includes `Image.open(f).save(g)` in Pillow, image editors, and CDN optimisers. Repack from the original bytes rather than re-saving the rendered file.

Trailers after IEND survive only byte-exact transports. The chunk carrier survives only pipelines that preserve ancillary `iTXt` chunks — many do, a plain Pillow re-save does not (measured). If the file must travel through an image pipeline — a chat attachment, a thumbnailer, a transcoder — use the chunk carrier (pack does), and upload it as a file rather than as an inline image so the PNG is not transcoded to JPEG.

## Working with a human

The human sees the PNG — the simplified, visual layer. All depth (svg source, doc, manifest) is invisible to casual viewing but fully present for you. When the human asks about the image, unpack and read the doc for context. When the human wants the picture changed, edit the svg and repack visual. When the human wants depth added, edit the doc and repack text-only. The file is the whole exchange — one artifact, both modalities, no side channels.
