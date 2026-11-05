# sharethought

A PNG file with layers. The image renders normally everywhere — that's what humans see. Carried inside the file: the SVG source it was rendered from, a markdown document, and a manifest that indexes both and carries its own unpack/repack instructions.

One file. Two audiences. The human reads the picture; the LLM reads the depth. Both edit the same artifact.

## The format

Valid PNG, dual carrier: `iTXt` chunks inside the PNG chunk stream (survives image pipelines that re-serialise the PNG) **and** a `SHARETHOUGHT-v1` trailer after IEND (greppable, byte-exact transports). Manifest + delimiter-framed text sections (`svg`, `doc`, any others). Full spec in [SKILL.md](SKILL.md).

## Use it

```
python3 sharethought.py pack   --png base.png --svg s.svg --doc d.md -o out.png --what "one line"
python3 sharethought.py unpack out.png
python3 sharethought.py repack --text out.png --set doc=new.md -o out2.png
python3 sharethought.py verify out.png
python3 -m unittest -q test_sharethought
```

Stdlib only — no dependencies. Drop `SKILL.md` into your agent's skill directory (Claude Code, opencode, any harness that reads the SKILL.md convention), or just read it.

## Repo layout

```
SKILL.md               the skill — spec + procedures
sharethought.py        reference implementation (pack/unpack/repack/verify, stdlib only)
test_sharethought.py   test suite: round-trips, transport survival, malformed inputs
LICENSE                free for any use; no fencing
```

## Verify

`python3 sharethought.py verify file.png` — checks signature, IEND, both carriers, agreement between them, name grammar, and manifest↔section consistency. Any sharethought file also opens as a normal image in any viewer.
