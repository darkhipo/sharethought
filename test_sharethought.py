"""Tests for sharethought.py — run: python3 -m unittest -q test_sharethought"""

import struct
import unittest
import zlib

import sharethought as st


def make_png(w=2, h=2) -> bytes:
    """Minimal valid PNG (solid red) built from scratch."""
    def chunk(typ, data):
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return st.PNG_SIG + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>'
DOC = "# Doc\n\nDepth layer.\n"


class RoundTrip(unittest.TestCase):
    def test_pack_unpack_dual_carrier(self):
        art = st.pack(make_png(), SVG, DOC, "test artifact")
        manifest, sections, carrier = st.unpack(art)
        self.assertEqual(carrier, "chunks+trailer")
        self.assertEqual(sections["svg"], SVG)
        self.assertEqual(sections["doc"], DOC)
        self.assertEqual(manifest["what"], "test artifact")
        self.assertIn("PASS", st.verify(art))

    def test_repack_text_roundtrip(self):
        art = st.pack(make_png(), SVG, DOC)
        art2 = st.repack_text(art, {"doc": "# New doc\n"})
        _, sections, _ = st.unpack(art2)
        self.assertEqual(sections["doc"], "# New doc\n")
        self.assertEqual(sections["svg"], SVG)
        self.assertIn("PASS", st.verify(art2))


class TransportSurvival(unittest.TestCase):
    """The case study: pipelines strip post-IEND bytes or re-serialise chunks."""

    def test_trailer_stripped_chunks_survive(self):
        art = st.pack(make_png(), SVG, DOC)
        stripped = art[: st.find_iend(art)]  # platform drops everything after IEND
        manifest, sections, carrier = st.unpack(stripped)
        self.assertEqual(carrier, "chunks")
        self.assertEqual(sections["doc"], DOC)

    def test_chunks_stripped_trailer_survives(self):
        art = st.pack(make_png(), SVG, DOC)
        prefix, iend, trailer = st.split_artifact(art)  # prefix has carrier chunks removed
        rebuilt = prefix + iend + trailer
        manifest, sections, carrier = st.unpack(rebuilt)
        self.assertEqual(carrier, "trailer")
        self.assertEqual(sections["svg"], SVG)

    def test_no_payload_errors(self):
        with self.assertRaises(st.SharethoughtError):
            st.unpack(make_png())


class Malformed(unittest.TestCase):
    def test_bad_signature(self):
        with self.assertRaises(st.SharethoughtError):
            st.unpack(b"JUNKJUNK" + st.pack(make_png(), SVG, DOC)[8:])

    def test_truncated_png_no_iend(self):
        art = st.pack(make_png(), SVG, DOC)
        with self.assertRaises(st.SharethoughtError):
            st.find_iend(art[: st.find_iend(art) - 4])  # cut into IEND's CRC

    def test_duplicate_marker(self):
        art = st.pack(make_png(), SVG, DOC)
        i = st.find_iend(art)
        doubled = art + st.MARKER + b"\n{}"  # second marker inside trailer
        with self.assertRaises(st.SharethoughtError):
            st.parse_trailer(doubled[i:])

    def test_bad_section_name_rejected_at_pack(self):
        manifest = {"v": 1, "what": "x", "how": {}, "sections": [
            {"name": "my-notes", "type": "text/markdown", "enc": "utf8", "summary": "s"}]}
        with self.assertRaises(st.SharethoughtError):
            st.build_trailer(manifest, {"my-notes": "x"})

    def test_bad_section_name_flagged_at_unpack(self):
        body = b"SHARETHOUGHT-v1\n" + b'{"v":1,"sections":[]}' + b"\n<<<SECTION:my-notes>>>\nx\n<<<END:my-notes>>>\n"
        with self.assertRaises(st.SharethoughtError) as cm:
            st.parse_trailer(body)
        self.assertIn("my-notes", str(cm.exception))

    def test_unbalanced_delimiters(self):
        body = (b"SHARETHOUGHT-v1\n"
                b'{"v":1,"sections":[{"name":"doc","type":"text/markdown","enc":"utf8","summary":"s"}]}\n'
                b"<<<SECTION:doc>>>\nhello\n<<<END:svg>>>\n")
        with self.assertRaises(st.SharethoughtError) as cm:
            st.parse_trailer(body)
        self.assertIn("unbalanced", str(cm.exception))

    def test_orphaned_trailing_content(self):
        art = st.pack(make_png(), SVG, DOC)
        with self.assertRaises(st.SharethoughtError) as cm:
            st.parse_trailer(art[st.find_iend(art):] + b"stray junk\n")
        self.assertIn("orphaned", str(cm.exception))

    def test_manifest_lists_missing_section(self):
        body = (b"SHARETHOUGHT-v1\n"
                b'{"v":1,"sections":[{"name":"doc","enc":"utf8","summary":"s"},{"name":"ghost","enc":"utf8","summary":"s"}]}\n'
                b"<<<SECTION:doc>>>\nhello\n<<<END:doc>>>\n")
        with self.assertRaises(st.SharethoughtError) as cm:
            st.parse_trailer(body)
        self.assertIn("ghost", str(cm.exception))

    def test_carriers_disagree_errors(self):
        art = st.pack(make_png(), SVG, DOC)
        iend_end = st.find_iend(art)
        trailer = art[iend_end:]
        tampered = trailer.replace(b"Depth layer.", b"Tampered.")  # chunks left intact
        with self.assertRaises(st.SharethoughtError) as cm:
            st.unpack(art[:iend_end] + tampered)
        self.assertIn("disagree", str(cm.exception))


class DelimiterSafety(unittest.TestCase):
    def test_own_end_delimiter_auto_base64(self):
        hostile = f"# Doc\n\nContains its own closer:\n<<<END:doc>>>\nmore text\n{st.MARKER.decode()}\n"
        art = st.pack(make_png(), SVG, hostile)
        _, sections, _ = st.unpack(art)
        self.assertEqual(sections["doc"], hostile)  # literal text survives intact
        self.assertIn("PASS", st.verify(art))

    def test_unknown_enc_rejected(self):
        manifest = {"v": 1, "what": "x", "how": {}, "sections": [
            {"name": "doc", "type": "text/markdown", "enc": "rot13", "summary": "s"}]}
        with self.assertRaises(st.SharethoughtError):
            st.build_trailer(manifest, {"doc": "x"})


if __name__ == "__main__":
    unittest.main()
