# sharethought

A PNG file with layers. The image renders normally everywhere — that's what humans see. Carried inside the file: the SVG source it was rendered from, a markdown document, and a manifest that indexes both and carries its own unpack/repack instructions.

One file. Two audiences. The human reads the picture; the LLM reads the depth. Both edit the same artifact.

## One file, zero dependencies

[SKILL.md](SKILL.md) is the entire product: the format spec plus working shell recipes for pack, unpack, repack, and verify. No scripts to install, no runtime, no interpreter. Everything runs on a basic shell plus early-generation utilities — `grep`, `od`, `awk`, `gzip`, `base64`, `tail`, `head`. The reading agent is the parser; the shell only moves bytes.

Two tricks make this work without libraries:

- **CRC32 without zlib**: `gzip` streams end with the CRC32 of their input — `gzip | tail -c 8 | head -c 4 | od` is a pure-shell CRC32 oracle, which is exactly what writing a PNG `iTXt` chunk needs.
- **One payload, two containers**: the same payload bytes live in a greppable trailer after IEND and in one uncompressed `iTXt` chunk before it — so one delimiter-based parser reads either, no decompression anywhere.

## The format

Valid PNG, dual carrier. Payload = `SHARETHOUGHT-v1` marker + JSON manifest + `<<<SECTION:name>>>`-framed text sections (`svg`, `doc`, any others). The chunk carrier survives pipelines that preserve ancillary chunks; the trailer survives byte-exact transports. Full spec: [SKILL.md](SKILL.md).

## Use it

Drop `SKILL.md` into your agent's skill directory (any harness that reads the SKILL.md convention), or just read it — every recipe it contains is copy-paste runnable.

Quick check on any file: `grep -abo 'SHARETHOUGHT-v1$' file.png` finds the payload (end-of-line anchored — every hit is a payload copy); `tail -c +$((offset+1)) file.png` prints it.

## Repo layout

```
SKILL.md    the format spec + all shell recipes — the product
test.sh     recipe tests: both locales (C, C.utf8), fixture PNG via base64, no renderer needed
LICENSE     free for any use; no fencing
```

CI runs `sh test.sh` with the default awk and again with `AWK=gawk` — byte-emitting awk calls are locale-sensitive in gawk (numeric `%c` becomes a codepoint under UTF-8), which is exactly the class the harness pins.
