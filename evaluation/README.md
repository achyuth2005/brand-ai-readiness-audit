# Evaluation evidence

The raw records behind the numbers in the main README's *Evaluation results*. All runs used
the engine exactly as it is in this repository (`lib/`, `skills/`, `config/`, `schema/` are
byte-identical to the evaluated copy) and the dependency versions in `requirements.txt`, on
Python 3.11.6.

## `out-of-sample/`: 47 sites the engine had never seen (2026-09-27)

| File | What it is |
| :-- | :-- |
| `sites.csv` | the 47 inputs: 42 sites plus 5 malformed-input variants |
| `results.md` | per-site exit code, runtime, severity counts and critical/high findings; per-check fire rates |
| `labels.md` | every critical and high finding from the 36 published reports (57), labelled by hand against the stored response bytes as correct, overstated, false positive or unclear, with the reason; tally at the end |
| `determinism.tsv` | each stored crawl re-analysed offline twice: `offline_x2_identical` compares the two `audit.json` files byte for byte; `findings_match_live` compares (check, severity, evidence string) with the live run |

The stored crawls themselves (over 5,000 files of third-party pages) are not included. The
last column of `determinism.tsv` says `NO` for 4 sites because the evidence string of one
finding (`G0.HTTP.REDIRECT_CHAIN`) counts off-site entries in offline mode ("1 of 63" vs
"1 of 75"); the (check, severity) sets match on all 36.

`determinism-recheck-2026-11-18.tsv` repeats the offline re-analysis seven weeks later from
this repository with the pinned `requirements.txt`. 35 of 36 sites were byte-identical across
both runs and to the September reports. The exception, `nextjsdocs`, shows a real limit:
its `engagement-audit` needs about 90 s, which is exactly the specialist time cap
(`crawl.analyzer_timeout_seconds` in `config/thresholds.json`). In September it timed out
on every run (live and both re-analyses), so those three reports matched. On the re-check it
timed out once (90.02 s, 10 findings, identical to September) and finished once (89.49 s,
21 findings). Output is deterministic only while every specialist finishes clearly inside
its cap; a timed-out specialist is always recorded in `coverage.skills_failed`.

## `cnet/`: the same core findings across engine-only and agent runs (2026-09-27)

`reports/` holds the five `audit.json` files from five independent live audits of
https://www.cnet.com/: two by the engine alone (1, 2) and three by Claude Code 2.1.282 driving
the `ai-readiness-audit` skill (3: Claude Opus 5.5, 4: Claude Sonnet 5, 5: Claude Opus 5.5,
the recorded run). `core_findings_cnet.json` lists the 18 (check, severity) pairs found in
runs 1 to 4; run 5 came later and is the independent check.

```bash
for f in evaluation/cnet/reports/*.json; do python3 evaluation/cnet/compare_core.py "$f"; done
```

Each prints `core present: 18/18` and `PASS`. The checker exits non-zero if any core finding
is missing.
