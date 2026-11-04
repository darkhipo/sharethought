# sharethought

A PNG file with layers. The image renders normally everywhere — that's what humans see. Appended behind the PNG's end marker: the SVG source it was rendered from, a markdown document, and a manifest that indexes both and carries its own unpack/repack instructions.

One file. Two audiences. The human reads the picture; the LLM reads the depth. Both edit the same artifact.

## The format

Valid PNG → `SHARETHOUGHT-v1` marker → JSON manifest → delimiter-framed text sections (`svg`, `doc`, any others). Full spec in [SKILL.md](SKILL.md).

## Use it

Drop `SKILL.md` into your agent's skill directory (Claude Code, opencode, any harness that reads the SKILL.md convention). Or just read it — the spec is short enough to load into any context window.

## Repo layout

```
SKILL.md    the skill — spec + unpack/create/repack procedures
LICENSE     free for any use; no fencing
```

## Verify

Any sharethought file: opens as a normal image, and `strings file.png | grep SHARETHOUGHT` shows the trailer. That's the whole test.
