#!/usr/bin/env python3
"""sharethought v0.2 — reference implementation (stdlib only).

A sharethought file is a valid PNG carrying a manifest plus text sections
(`svg` source, `doc` markdown, ...) in TWO carriers:

  trailer : b"SHARETHOUGHT-v1\\n" + JSON manifest + delimiter-framed sections,
            appended after the IEND chunk. Greppable; writable with
            `cat trailer.txt >> file.png`. Survives byte-exact transports only.
  chunks  : iTXt ancillary chunks (keyword "sharethought:manifest",
            "sharethought:<name>"), zlib-compressed, inserted before IEND.
            Survives image pipelines that re-serialise the PNG.

pack writes both carriers from one build (they cannot diverge);
unpack prefers chunks and falls back to the trailer; verify checks that
both carriers parse and agree. Disagreement between carriers raises —
pack cannot produce it, so it means external tampering.

Usage:
    python3 sharethought.py pack   --png base.png --svg s.svg --doc d.md -o out.png
    python3 sharethought.py unpack out.png [--out-dir DIR]
    python3 sharethought.py repack --text out.png --set doc=new.md -o out2.png
    python3 sharethought.py verify out.png
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import sys
import zlib
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"
MARKER = b"SHARETHOUGHT-v1"
KW_PREFIX = "sharethought:"
KW_MANIFEST = KW_PREFIX + "manifest"
NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
SECTION_RE = re.compile(rb"<<<SECTION:(\w+)>>>\n(.*?)\n<<<END:\1>>>", re.DOTALL)
BAD_NAME_RE = re.compile(rb"<<<SECTION:([^>\n]+?)>>>")


class SharethoughtError(Exception):
    """Raised for any malformed or non-sharethought input."""


# ---------------------------------------------------------------- PNG chunks

def iter_chunks(raw: bytes):
    """Yield (type, data) for each chunk up to and including IEND."""
    if not raw.startswith(PNG_SIG):
        raise SharethoughtError("not a PNG (bad signature)")
    off = 8
    while off + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[off : off + 4])
        typ = raw[off + 4 : off + 8]
        end = off + 12 + length
        if end > len(raw):
            raise SharethoughtError(f"chunk {typ!r} overruns file (truncated PNG)")
        yield typ, raw[off + 8 : off + 8 + length]
        if typ == b"IEND":
            return
        off = end
    raise SharethoughtError("no IEND chunk (truncated PNG)")


def find_iend(raw: bytes) -> int:
    """Return the byte offset just past the IEND chunk."""
    off = 8
    while off + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[off : off + 4])
        typ = raw[off + 4 : off + 8]
        end = off + 12 + length
        if end > len(raw):
            raise SharethoughtError(f"chunk {typ!r} overruns file (truncated PNG)")
        if typ == b"IEND":
            return end
        off = end
    raise SharethoughtError("no IEND chunk (truncated PNG)")


def _crc_chunk(typ: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data))


# ---------------------------------------------------------------- trailer

def validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise SharethoughtError(f"invalid section name {name!r}: must match [A-Za-z0-9_]+")
    if len(KW_PREFIX + name) > 79:
        raise SharethoughtError(f"section name {name!r} too long for an iTXt keyword")


def _enc_body(name: str, body: str, enc: str) -> tuple[str, str]:
    """Encode a section body; auto-upgrade utf8 to base64 when unsafe.

    The upgrade triggers on any line that IS the section's closer — first
    line, middle, or last — plus any occurrence of the marker string.
    """
    if enc not in ("utf8", "base64"):
        raise SharethoughtError(f"section {name!r}: unknown enc {enc!r} (use utf8|base64)")
    closer = f"<<<END:{name}>>>"
    if enc == "utf8" and (any(ln == closer for ln in body.split("\n")) or MARKER.decode() in body):
        enc = "base64"  # body would break its own framing — carry it safely
    out = base64.b64encode(body.encode()).decode() if enc == "base64" else body
    return out, enc


def build_trailer(manifest: dict, sections: dict[str, str]) -> bytes:
    """Build the post-IEND trailer. Mutates manifest encs when auto-base64ing.

    The encoding pass runs BEFORE the manifest is serialized, so the embedded
    manifest always reflects the final encs (and matches the chunk carrier).
    """
    listed = [s["name"] for s in manifest.get("sections", [])]
    if sorted(listed) != sorted(sections):
        raise SharethoughtError(f"manifest sections {listed} != provided {sorted(sections)}")
    bodies: dict[str, str] = {}
    for meta in manifest["sections"]:
        name = meta["name"]
        validate_name(name)
        bodies[name], meta["enc"] = _enc_body(name, sections[name], meta.get("enc", "utf8"))
    parts = [MARKER + b"\n", json.dumps(manifest, indent=1).encode(), b"\n"]
    for meta in manifest["sections"]:
        name = meta["name"]
        parts.append(f"<<<SECTION:{name}>>>\n{bodies[name]}\n<<<END:{name}>>>\n".encode())
    return b"".join(parts)


def parse_trailer(trailer: bytes) -> tuple[dict, dict[str, str]]:
    """Parse the trailer into (manifest, {name: body}). Strict."""
    if not trailer.startswith(MARKER + b"\n"):
        raise SharethoughtError("trailer does not start with the SHARETHOUGHT-v1 marker")
    for m in BAD_NAME_RE.finditer(trailer):
        if not NAME_RE.match(m.group(1).decode(errors="replace")):
            raise SharethoughtError(
                f"invalid section name {m.group(1).decode(errors='replace')!r}: must match [A-Za-z0-9_]+"
            )
    rest = trailer[trailer.index(b"\n") + 1 :]
    manifest, consumed = json.JSONDecoder().raw_decode(rest.decode("utf-8", errors="strict"))
    if not isinstance(manifest, dict):
        raise SharethoughtError("manifest is not a JSON object")
    secs_meta = manifest.get("sections", [])
    if not isinstance(secs_meta, list):
        raise SharethoughtError("manifest 'sections' is not a list")

    # No global OPEN/CLOSE counting: a foreign section's closer inside a body
    # is body content, not framing. Structural errors surface as unmatched
    # regex sections + orphaned leftover below, with named errors.

    sections: dict[str, str] = {}
    spent = rest[consumed:]  # everything after the manifest JSON; matches get carved out
    for m in SECTION_RE.finditer(trailer):
        name, raw_body = m.group(1).decode(), m.group(2)
        if name in sections:
            raise SharethoughtError(f"duplicate section {name!r} in trailer")
        spent = spent.replace(m.group(0), b"", 1)
        meta = next((s for s in secs_meta if s.get("name") == name), None)
        if meta is None:
            raise SharethoughtError(f"section {name!r} present in trailer but not in manifest")
        enc = meta.get("enc", "utf8")
        if enc == "base64":
            try:
                sections[name] = base64.b64decode(raw_body).decode()
            except Exception as exc:
                raise SharethoughtError(f"section {name!r}: bad base64 body ({exc})") from exc
        elif enc == "utf8":
            sections[name] = raw_body.decode()
        else:
            raise SharethoughtError(f"section {name!r}: unknown enc {enc!r}")
    leftover = spent.strip()
    if leftover:
        if MARKER in leftover:
            raise SharethoughtError("duplicate SHARETHOUGHT-v1 marker in orphaned trailer content")
        raise SharethoughtError(f"orphaned trailer content: {leftover[:80]!r}...")
    _check_consistency(manifest, sections)
    return manifest, sections


# ---------------------------------------------------------------- iTXt carrier

def _check_consistency(manifest: dict, sections: dict[str, str]) -> None:
    """Manifest <-> sections consistency, enforced identically for every carrier."""
    secs = manifest.get("sections")
    if not isinstance(secs, list):
        raise SharethoughtError("manifest 'sections' is not a list")
    listed = [s.get("name") if isinstance(s, dict) else None for s in secs]
    if None in listed:
        raise SharethoughtError("manifest 'sections' entry without a name")
    dupes = sorted({n for n in listed if listed.count(n) > 1})
    if dupes:
        raise SharethoughtError(f"manifest lists duplicate section(s): {dupes}")
    missing = [n for n in listed if n not in sections]
    if missing:
        raise SharethoughtError(f"manifest lists sections missing from payload: {missing}")
    extra = [n for n in sections if n not in listed]
    if extra:
        raise SharethoughtError(f"sections not listed in manifest: {extra}")


def _chunks_to_payload(chunked: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Validate and convert the iTXt carrier to (manifest, sections)."""
    if KW_MANIFEST not in chunked:
        raise SharethoughtError("chunk carrier present but sharethought:manifest chunk missing")
    try:
        manifest = json.loads(chunked[KW_MANIFEST])
    except json.JSONDecodeError as exc:
        raise SharethoughtError(f"sharethought:manifest chunk is not valid JSON ({exc})") from exc
    if not isinstance(manifest, dict):
        raise SharethoughtError("manifest chunk is not a JSON object")
    sections = {k[len(KW_PREFIX):]: v for k, v in chunked.items() if k != KW_MANIFEST}
    _check_consistency(manifest, sections)
    return manifest, sections


def make_itxt(keyword: str, text: str) -> bytes:
    """One zlib-compressed iTXt chunk carrying text under keyword."""
    if len(keyword.encode()) > 79:
        raise SharethoughtError(f"iTXt keyword too long: {keyword!r}")
    payload = keyword.encode() + b"\x00\x01\x00" + b"\x00\x00" + zlib.compress(text.encode())
    return _crc_chunk(b"iTXt", payload)


def read_itxt(data: bytes) -> tuple[str, str]:
    """Parse one iTXt chunk payload into (keyword, text)."""
    key, sep, rest = data.partition(b"\x00")
    if not sep or len(rest) < 2:
        raise SharethoughtError("malformed iTXt chunk (no keyword terminator)")
    flag, method, rest = rest[0], rest[1], rest[2:]
    if method != 0:
        raise SharethoughtError(f"unknown iTXt compression method {method}")
    _, sep1, rest = rest.partition(b"\x00")  # language tag
    _, sep2, text = rest.partition(b"\x00")  # translated keyword
    if not (sep1 and sep2):
        raise SharethoughtError("malformed iTXt chunk (missing tag terminators)")
    if flag == 1:
        try:
            text = zlib.decompress(text)
        except zlib.error as exc:
            raise SharethoughtError(f"bad zlib in iTXt chunk ({exc})") from exc
    elif flag != 0:
        raise SharethoughtError(f"unknown iTXt compression flag {flag}")
    return key.decode(), text.decode()


def read_chunks(raw: bytes) -> dict[str, str]:
    """Collect sharethought:* iTXt entries. Empty dict if none."""
    out: dict[str, str] = {}
    for typ, data in iter_chunks(raw):
        if typ == b"iTXt":
            key, text = read_itxt(data)
            if key.startswith(KW_PREFIX):
                name = key[len(KW_PREFIX) :]
                if name == "manifest" or NAME_RE.match(name):
                    if key in out:
                        raise SharethoughtError(f"duplicate iTXt chunk keyword {key!r}")
                    out[key] = text
                else:
                    raise SharethoughtError(f"invalid section name in chunk keyword: {name!r}")
    return out


def assemble(png_prefix_with_sig: bytes, iend_chunk: bytes, manifest: dict,
             sections: dict[str, str]) -> bytes:
    """Build a dual-carrier artifact from cleaned parts.

    Trailer is built FIRST: build_trailer may auto-upgrade a section to
    base64 (mutating its manifest enc), and the chunk carrier must carry
    the mutated manifest so the two carriers always agree.
    """
    trailer = build_trailer(manifest, sections)
    chunk_carrier = make_itxt(KW_MANIFEST, json.dumps(manifest))
    for meta in manifest["sections"]:
        chunk_carrier += make_itxt(KW_PREFIX + meta["name"], sections[meta["name"]])
    return png_prefix_with_sig + chunk_carrier + iend_chunk + trailer


def split_artifact(raw: bytes) -> tuple[bytes, bytes, bytes]:
    """Split into (prefix_without_carrier_chunks, iend_chunk, trailer_bytes)."""
    find_iend(raw)  # validates
    keep, iend = [], b""
    for typ, data in iter_chunks(raw):
        if typ == b"iTXt":
            key, _ = read_itxt(data)
            if key.startswith(KW_PREFIX):
                continue  # drop old carrier; assemble() rewrites it
        if typ == b"IEND":
            iend = _crc_chunk(b"IEND", data)
            break
        keep.append(_crc_chunk(typ, data))
    iend_end = find_iend(raw)
    return PNG_SIG + b"".join(keep), iend, raw[iend_end:]


# ---------------------------------------------------------------- API

def unpack(raw: bytes) -> tuple[dict, dict[str, str], str]:
    """Unpack any sharethought artifact. Returns (manifest, sections, carrier)."""
    chunked = read_chunks(raw)
    trailer_ok = False
    try:
        iend_end = find_iend(raw)
        trailer_ok = raw[iend_end:].startswith(MARKER + b"\n")
    except SharethoughtError:
        pass
    if chunked and trailer_ok:
        t_manifest, t_sections = parse_trailer(raw[iend_end:])
        c_manifest, c_sections = _chunks_to_payload(chunked)
        if t_manifest != c_manifest or t_sections != c_sections:
            raise SharethoughtError(
                "chunk and trailer carriers disagree — the file was edited inconsistently"
            )
        return t_manifest, t_sections, "chunks+trailer"
    if chunked:
        manifest, sections = _chunks_to_payload(chunked)
        return manifest, sections, "chunks"
    if trailer_ok:
        manifest, sections = parse_trailer(raw[iend_end:])
        return manifest, sections, "trailer"
    raise SharethoughtError(
        "no sharethought payload: neither iTXt carrier chunks nor a SHARETHOUGHT-v1 trailer found"
    )


def pack(png: bytes, svg: str, doc: str, what: str = "") -> bytes:
    """Pack a base PNG with svg+doc sections into a dual-carrier artifact."""
    for name, body in (("svg", svg), ("doc", doc)):
        validate_name(name)
    manifest = {
        "v": 1,
        "what": what or "sharethought artifact",
        # NOTE: these strings are duplicated verbatim in SKILL.md's manifest
        # example — test_how_matches_spec pins them together.
        "how": {
            "unpack": "Read the iTXt chunks named sharethought:* if present (the manifest "
                      "is sharethought:manifest); otherwise read bytes from the "
                      "SHARETHOUGHT-v1 marker to EOF. Parse the JSON manifest, then each "
                      "section between its <<<SECTION:name>>> and <<<END:name>>> "
                      "delimiters. Decode base64 sections.",
            "repack_text_only": "Keep the original PNG bytes up to the marker, then "
                                "rewrite the trailer with updated manifest and sections. "
                                "No re-render needed. Or run: python3 sharethought.py "
                                "repack --text FILE --set doc=new.md",
            "repack_visual": "To change the image: edit the svg section, render it to PNG "
                             "(rsvg-convert -w 1200 in.svg > out.png, or cairosvg, or "
                             "resvg, or a headless browser such as Chromium via "
                             "Playwright), then pack again.",
        },
        "sections": [
            {"name": "svg", "type": "image/svg+xml", "enc": "utf8", "summary": "rendered image source"},
            {"name": "doc", "type": "text/markdown", "enc": "utf8", "summary": "depth document"},
        ],
    }
    prefix, iend, _ = split_artifact(png)
    return assemble(prefix, iend, manifest, {"svg": svg, "doc": doc})


def repack_text(raw: bytes, new_sections: dict[str, str]) -> bytes:
    """Replace section bodies (no re-render); rebuilds both carriers."""
    manifest, sections, _ = unpack(raw)
    for name, body in new_sections.items():
        validate_name(name)
        if name not in sections:
            raise SharethoughtError(f"cannot repack unknown section {name!r}")
        sections[name] = body
    prefix, iend, _ = split_artifact(raw)
    return assemble(prefix, iend, manifest, sections)


def verify_png_crcs(raw: bytes) -> int:
    """Recompute every chunk CRC; return the chunk count. Raises on mismatch."""
    off, n = 8, 0
    while off + 8 <= len(raw):
        (ln,) = struct.unpack(">I", raw[off : off + 4])
        typ = raw[off + 4 : off + 8]
        end = off + 12 + ln
        if end > len(raw):
            raise SharethoughtError(f"chunk {typ!r} overruns file (truncated PNG)")
        (stored,) = struct.unpack(">I", raw[end - 4 : end])
        if zlib.crc32(typ + raw[off + 8 : off + 8 + ln]) != stored:
            raise SharethoughtError(f"chunk {typ!r} (#{n}): CRC mismatch — corrupt PNG")
        n += 1
        if typ == b"IEND":
            return n
        off = end
    raise SharethoughtError("no IEND chunk (truncated PNG)")


def verify(raw: bytes) -> list[str]:
    """Run all checks; return the report lines (raises on any failure)."""
    if len(raw) < 24 or not raw.startswith(PNG_SIG):
        raise SharethoughtError("not a PNG (bad signature)")
    (w, h) = struct.unpack(">II", raw[16:24])
    lines = [f"PNG signature ok; IHDR {w}x{h}"]
    lines.append(f"{verify_png_crcs(raw)} chunks, CRCs ok")
    iend_end = find_iend(raw)
    lines.append(f"IEND chunk ok at offset {iend_end - 12}")
    manifest, sections, carrier = unpack(raw)
    lines.append(f"carrier: {carrier}")
    for key in ("v", "what", "how", "sections"):
        if key not in manifest:
            raise SharethoughtError(f"manifest missing required key {key!r}")
    for meta in manifest["sections"]:
        for field in ("name", "type", "enc", "summary"):
            if field not in meta:
                raise SharethoughtError(f"manifest section {meta.get('name')!r} missing {field!r}")
        validate_name(meta["name"])
    for name, body in sorted(sections.items()):
        lines.append(f"section {name}: {len(body)} chars")
    if carrier == "chunks+trailer":
        lines.append("carriers agree")
    lines.append("PASS")
    return lines


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sharethought", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pack", help="build a dual-carrier artifact from a rendered PNG")
    sp.add_argument("--png", required=True, help="base PNG (already rendered from the SVG)")
    sp.add_argument("--svg", required=True)
    sp.add_argument("--doc", required=True)
    sp.add_argument("--what", default="", help="one-line description for the manifest")
    sp.add_argument("-o", "--out", required=True)

    su = sub.add_parser("unpack", help="write manifest + sections to files")
    su.add_argument("file")
    su.add_argument("--out-dir", default=None)

    sr = sub.add_parser("repack", help="replace section bodies without re-rendering")
    sr.add_argument("--text", required=True, dest="file", help="artifact to edit")
    sr.add_argument("--set", action="append", default=[], metavar="name=FILE",
                    help="replace section body from FILE (repeatable)")
    sr.add_argument("-o", "--out", required=True)

    sv = sub.add_parser("verify", help="validate an artifact")
    sv.add_argument("file")

    a = p.parse_args(argv)
    try:
        if a.cmd == "pack":
            out = pack(Path(a.png).read_bytes(), Path(a.svg).read_text(), Path(a.doc).read_text(), a.what)
            Path(a.out).write_bytes(out)
            print(f"wrote {a.out} ({len(out)} bytes, dual carrier)")
        elif a.cmd == "unpack":
            manifest, sections, carrier = unpack(Path(a.file).read_bytes())
            out_dir = Path(a.out_dir or (Path(a.file).stem + ".unpack"))
            out_dir.mkdir(exist_ok=True)
            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
            for name, body in sections.items():
                (out_dir / name).write_text(body)
            print(f"carrier: {carrier}; wrote manifest + {sorted(sections)} to {out_dir}/")
        elif a.cmd == "repack":
            raw = Path(a.file).read_bytes()
            new = {}
            for spec in a.set:
                name, _, path = spec.partition("=")
                new[name] = Path(path).read_text()
            Path(a.out).write_bytes(repack_text(raw, new))
            print(f"wrote {a.out} (sections replaced: {sorted(new)})")
        elif a.cmd == "verify":
            for line in verify(Path(a.file).read_bytes()):
                print(line)
    except (SharethoughtError, OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
