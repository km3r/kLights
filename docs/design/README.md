# Design notes

Working notes, measurements and rejected alternatives. Kept because the
expensive part of most of these was finding out, and the second-most expensive
part is finding out again.

These are **not** how-to documents. If you want to run something, start from
[the README](../../README.md) or [the runbook](../runbook.md).

| | |
|---|---|
| [`previz-optics.md`](previz-optics.md) | Why the previz looks the way it does — fog, beam gains, the mirror ball, what was measured and what is eyeballed |
| [`timecoded-shows.md`](timecoded-shows.md) | F19: shows driven by which track is playing and where in it — the decisions, the designer layout, the staged build |

Elsewhere, for the same reason:

| | |
|---|---|
| [`../ROADMAP.md`](../ROADMAP.md) | What was built in what order, and the decisions worth knowing |
| [`../../events/despacio/NOTES.md`](../../events/despacio/NOTES.md) | The QLC+ and APC40 era: mounting conventions, the virtual console, the routine list |
| [`../../spike/timing/FINDINGS.md`](../../spike/timing/FINDINGS.md) | Whether Python can hold a DMX clock, measured |
| [`../pipeline.md`](../pipeline.md) | The Art-Net architecture, and the retired BlenderDMX path |
| [`../../legacy/blenderdmx/README.md`](../../legacy/blenderdmx/README.md) | What the retired previz tools did, and why one of them is not to be trusted |

**The commit log is the other half of this.** Commit messages here are long on
purpose and are the first place to look for why something is the way it is.
