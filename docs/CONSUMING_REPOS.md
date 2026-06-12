# Wiring the dbcinator bot into a firmware repo

This is the consumer-side recipe. The bot itself — the composite action that
regenerates the DBC, pushes it onto the PR branch, and posts the structured
wire-contract diff — lives in this repo at `actions/dbc-bot`. A firmware repo
opts in with **one workflow file**.

## Prerequisites

The firmware repo must already have:

1. **The code-first CAN DSL pattern set up.** Each frame declared once in
   `Core/Inc/can/messages/*.def`; struct/encoder/decoder/descriptor all derive
   from that declaration via `can_codecs.hpp`. Reference: [AMS#362] (Phase 2a,
   landed) and [AMS#364] (Phase 2b).
2. **A host-side DBC dumper.** A CMake target — convention name `dbc_dump` —
   that builds a binary which walks the DSL's runtime descriptor table and
   prints DBC `BO_` / `SG_` rows on stdout. Reference: AMS's
   `tools/dbc_dump.cpp`, ~40 lines, message-agnostic.
3. **The committed DBC.** `docs/dbc/<name>.dbc` exists and is in `git`. The bot
   *maintains* this file; it does not create it from scratch on first run.
   Initialise it once by running the dump tool locally and committing the
   output.
4. **The bot's GitHub App installed** on the repo, with the org-level secrets
   `DBCINATOR_APP_ID` and `DBCINATOR_PRIVATE_KEY` accessible to it. Reference:
   `docs/APP_SETUP.md` in this repo.

## The workflow file

Drop this into the firmware repo as `.github/workflows/dbc-bot.yml`:

```yaml
name: dbc-bot

on:
  pull_request:
    branches: [main, dev]

permissions:
  contents: write          # bot pushes the regenerated DBC
  pull-requests: write     # bot posts the wire-contract diff comment

jobs:
  regenerate:
    runs-on: ubuntu-latest
    steps:
      - uses: isc-fs/IFS08-DBCinator/actions/dbc-bot@main
        with:
          app-id:       ${{ secrets.DBCINATOR_APP_ID }}
          private-key:  ${{ secrets.DBCINATOR_PRIVATE_KEY }}
          dbc-path:     docs/dbc/ams.dbc           # adjust per repo
```

That's it. The defaults match AMS's layout (`cmake-source-dir: tests/unit`,
`dump-target: dbc_dump`, `dump-binary-path: dbc_dump`). For a repo that builds
its dump tool differently, override the relevant inputs — see
`actions/dbc-bot/action.yml` for the full list.

## What the bot does on every PR

1. Skips silently on PRs from forks (push-back is impossible).
2. Mints a short-lived install token via the `dbcinator` App.
3. Checks out the PR HEAD with that token, full history (`fetch-depth: 0`).
4. Installs `cantools`, configures CMake host-build, builds the dump target.
5. Runs the dumper, writes `/tmp/regenerated.dbc`.
6. Compares against `dbc-path` byte-for-byte.
7. If different: copies the new DBC into the tree, commits as
   `dbcinator[bot]`, pushes onto the PR branch.
8. Posts (or edits in place) a structured wire-contract diff comment on the
   PR. Re-runs *edit* the same comment instead of stacking.

## What review looks like

For a PR that does not touch the wire contract, the bot posts a single green
line and no commit lands. For a PR that does, two things appear:

- A `chore(dbc): regenerate from .def files` commit by `dbcinator[bot]`,
  updating `docs/dbc/<name>.dbc`.
- A markdown comment summarising the change: messages added / removed, and
  per-signal `length` / `byte_order` / `is_signed` / `scale` / `offset` /
  `unit` deltas.

The reviewer's job is to confirm the wire-contract diff matches the intent of
the `.def` change. Bytes on the wire are no longer reverse-engineered from
code review of bit-shift loops.

## Hands-off rule

`docs/dbc/<name>.dbc` is **bot-maintained**. Do not hand-edit. A human commit
that modifies it will be overwritten by the bot on the next PR run. Edit the
relevant `.def` file in `Core/Inc/can/messages/` instead; the DBC follows.

If you need the bot to *not* run on a specific PR (e.g. a docs-only change
that nonetheless touches a `.def` because of CI lint), open the PR as a draft
or skip the workflow — the bot will pick up the regeneration on the next
push.

## Adopting in a new ECU repo

Mirror the AMS reference layout:

```
Core/Inc/can/
├── can_dsl.hpp          ← vendored verbatim from IFS08-CE-AMS
├── can_codecs.hpp       ← vendored verbatim
└── messages/
    ├── all_messages.inc
    └── <one .def per frame the repo transmits>

tools/
└── dbc_dump.cpp         ← vendored verbatim

docs/dbc/
└── <ecu>.dbc            ← committed; bot-maintained
```

Then add the workflow above. Reference: [ECU#24] (the cross-repo adoption
tracking issue).

[AMS#362]: https://github.com/isc-fs/IFS08-CE-AMS/pull/362
[AMS#364]: https://github.com/isc-fs/IFS08-CE-AMS/pull/364
[ECU#24]:  https://github.com/isc-fs/IFS08-CE-ECU/issues/24
