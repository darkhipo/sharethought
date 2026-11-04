---
name: sharethought
description: Pack layered data into a PNG. The image humans see is the rendered SVG surface; the SVG source and a markdown document ride inside the file as appended text sections behind a self-documenting manifest. Use when creating, unpacking, editing, or repacking sharethought files — self-contained artifacts for human-LLM collaborative exchange where the human reads the picture and the LLM reads the depth.
---

# sharethought

A sharethought file is a valid PNG with text sections appended after the IEND chunk. It opens and renders as a normal image everywhere. Behind the image: the SVG source it was rendered from, and a markdown document. A manifest at the front of the appended data indexes everything and carries the unpack/repack instructions inside the file itself.

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

The marker `SHARETHOUGHT-v1` starts on its own line immediately after the PNG's IEND chunk. Everything from the marker to EOF is the trailer. Standard sections: `svg` (the image source, `image/svg+xml`) and `doc` (markdown, `text/markdown`). Arbitrary additional sections are allowed — any name, any text type.

### Manifest schema

```json
{
  "v": 1,
  "what": "one line describing this artifact",
  "how": {
    "unpack": "Read bytes from the SHARETHOUGHT-v1 marker to EOF. Parse the JSON manifest (it ends at the first <<<SECTION line). Each section's content sits between its <<<SECTION:name>>> and <<<END:name>>> delimiters. Decode base64 sections before use.",
    "repack_text_only": "To edit doc or other text sections without changing the image: keep the original PNG bytes up to the marker, then rewrite the trailer with updated manifest and sections. No re-render needed.",
    "repack_visual": "To change the image: edit the svg section, render it to PNG (rsvg-convert -w 1200 in.svg > out.png, or cairosvg, or resvg, or any SVG renderer), then append a fresh trailer to the new PNG."
  },
  "sections": [
    { "name": "svg",  "type": "image/svg+xml", "enc": "utf8",    "summary": "one-line description of the image" },
    { "name": "doc",  "type": "text/markdown", "enc": "utf8",    "summary": "one-line description of the document" }
  ]
}
```

The `how` field is mandatory and must contain working instructions — the file teaches any cold reader to open it.

## Unpack

```python
raw = open("file.png", "rb").read()
i = raw.index(b"SHARETHOUGHT-v1")
trailer = raw[i:].decode("utf-8")
import json
manifest, _ = json.JSONDecoder().raw_decode(trailer[trailer.index("\n")+1:])
# sections:
import re
for m in re.finditer(r"<<<SECTION:(\w+)>>>\n(.*?)\n<<<END:\1>>>",
                     trailer, re.DOTALL):
    name, body = m.group(1), m.group(2)
    # if enc is base64: import base64; body = base64.b64decode(body)
```

With shell only: `tail -c +$(( $(grep -abo 'SHARETHOUGHT-v1' file.png | head -1 | cut -d: -f1) + 1 )) file.png` prints the trailer as text.

## Create

1. Write the SVG (the human-facing picture or diagram).
2. Render to PNG: `rsvg-convert -w 1208 diagram.svg > out.png` (or `cairosvg diagram.svg -o out.png`, or `resvg diagram.svg out.png`).
3. Write the markdown document.
4. Append the trailer:

```python
png = open("out.png", "rb").read()
svg = open("diagram.svg").read()
doc = open("notes.md").read()
manifest = { "v": 1, "what": "...", "how": { ... as above ... },
             "sections": [ {"name":"svg","type":"image/svg+xml","enc":"utf8","summary":"..."},
                          {"name":"doc","type":"text/markdown","enc":"utf8","summary":"..."} ] }
trailer = b"SHARETHOUGHT-v1\n" + json.dumps(manifest, indent=1).encode() + b"\n"
trailer += f"<<<SECTION:svg>>>\n{svg}\n<<<END:svg>>>\n".encode()
trailer += f"<<<SECTION:doc>>>\n{doc}\n<<<END:doc>>>\n".encode()
open("artifact.png", "wb").write(png + trailer)
```

The output renders as a normal PNG. Verify: open it in any image viewer (shows the image), then `strings artifact.png | grep SHARETHOUGHT` (shows the trailer exists).

## Repack

**Text-only edit** (doc changed, image unchanged): reuse the original PNG bytes (everything before `SHARETHOUGHT-v1`), write a new trailer with the edited section and updated manifest summaries. No renderer needed.

**Visual edit** (svg changed): render the modified SVG to a fresh PNG, then append the new trailer (updated svg section + doc + manifest). Any SVG renderer works.

Round-trip rule: a repacked file is a sharethought file. Same format, same marker, updated manifest.

## Delimiter safety

Section delimiters are `<<<SECTION:name>>>` and `<<<END:name>>>` on their own lines. If content could contain such a line (rare), set `"enc":"base64"` in the manifest for that section and base64 the body. SVG and markdown never produce these lines naturally.

## Working with a human

The human sees the PNG — the simplified, visual layer. All depth (svg source, doc, manifest) is invisible to casual viewing but fully present for you. When the human asks about the image, unpack and read the doc for context. When the human wants the picture changed, edit the svg and repack visual. When the human wants depth added, edit the doc and repack text-only. The file is the whole exchange — one artifact, both modalities, no side channels.
