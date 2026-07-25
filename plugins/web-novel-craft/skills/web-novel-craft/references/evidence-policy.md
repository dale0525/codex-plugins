# Video evidence research policy

## Completion states

- `candidate`: discovered but not reviewed.
- `selected_pending_transcript`: frozen into scope; complete text unavailable.
- `transcript_acquired`: captions or ASR cover the full media duration.
- `full_review_complete`: a reviewer read from first to last segment and mapped the argument.
- `deep_distilled`: full review plus timestamped claims, boundaries, counterexamples, a reusable procedure, and acceptance tests.
- `rejected`: excluded with a recorded reason.

A title, description, search snippet, channel reputation, or partial transcript can never support `deep_distilled`.

## Acquisition order

1. Obtain creator/platform captions with language and provenance.
2. If unavailable or incomplete, download authorized audio and run local ASR.
3. Record media URL/ID, title, channel, publication date, duration, acquisition time, caption kind, language, tool/version, model, media hash, and transcript coverage.
4. Preserve raw transcripts only in a non-distributed research workspace. Do not package full subtitles in the plugin.

Use `scripts/transcribe_media.py` for local media when appropriate. Human-check names, invented terms, numbers, negation, and any phrase used as a direct quote.

## Full-review record

For every selected video, create:

- metadata and provenance;
- first and last covered time, segment count, and gap/overlap notes;
- chronological chapter/argument map;
- timestamped claim ledger;
- short excerpts only when necessary, with paraphrase preferred;
- claim type and evidence level;
- applicability and non-applicability;
- failure boundaries and counterexamples;
- caption/ASR ambiguity notes;
- translation to web-novel practice;
- at least one complete executable procedure.

The procedure contract is:

```text
trigger -> inputs -> ordered steps(action + check) -> output
-> failure signals -> repair -> acceptance -> example -> counterexample
```

## Claim types and levels

Types:

- `demonstration`: observable process or worked example in the media;
- `craft_model`: instructor's conceptual model;
- `practitioner_experience`: creator/editor account of their own work;
- `platform_claim`: statement about platform behavior, terms, or audience;
- `business_claim`: sales, income, conversion, or market assertion;
- `opinion`: taste, forecast, or recommendation without demonstrated support.

Levels:

- `E1`: directly present in complete transcript/ASR with timestamp;
- `E2`: E1 plus worked example, on-screen artifact, or corroborating official source;
- `E3`: plausible synthesis across multiple E1/E2 sources; label as synthesis;
- `E4`: hypothesis requiring a project-specific test.

E1 means the speaker said it, not that the world claim is independently true. Platform, legal, financial, health, and current-tool claims require fresh authoritative verification before operational use.

## Visual evidence boundary

Transcript evidence supports speech only. Do not infer diagrams, gestures, screen text, before/after pages, or UI states without sampled frames or a full visual review. Record `visual_review: none|sampled|complete`.

## Distillation rules

- Convert advice into decisions, actions, checks, and failure signals.
- Preserve scope: short-story compression, classroom exercise, one author's workflow, and platform history are not universal laws.
- Keep tensions rather than forcing false consensus. For example, discovery writing can coexist with a strict system-rule bible.
- Prefer mechanisms over slogans: explain how a technique changes reader expectation, information, choice, or production risk.
- Include at least one counterexample where following the advice mechanically would make the work worse.
- Do not imitate a living author's distinctive voice; abstract craft mechanisms and help the user develop their own voice.

## Corpus validation

Run from the shared skill root:

```bash
python scripts/validate_corpus.py
```

The corpus passes only when every source frozen in the base, extension, and priority-234 manifests has matching ASR/transcript evidence and a `deep_distilled` knowledge entry with timestamped claims, boundaries, and at least one complete procedure.
