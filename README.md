# IFS08-DBCinator

Home of the **dbcinator** GitHub App and the reusable automation that
keeps every IFS08 firmware repo's CAN database (`docs/dbc/*.dbc`) in
lockstep with its own code-first DSL.

Each firmware repo (`IFS08-CE-AMS`, `IFS08-CE-ECU`, `IFS08-CE-UDV`,
`IFS08-CE-DASH`) declares the byte layout of each frame it transmits
in `Core/Inc/can/messages/*.def` files. A small host tool in that
repo (`tools/dbc_dump.cpp`) walks the DSL's runtime descriptor table
and emits the matching DBC. The dbcinator bot, defined here, runs on
every PR in the consumer repo to regenerate that DBC, commit any
diff onto the PR branch, and post a structured wire-contract diff
comment. The firmware code is the only source of truth; the DBC is a
derived artefact.

## What lives here

| Path | What |
|---|---|
| [`actions/dbc-bot/`](actions/dbc-bot/) | The reusable composite action consumer repos invoke from their own `.github/workflows/dbc-bot.yml`. Mints a short-lived install token via the dbcinator App, builds the consumer's `dbc_dump` target host-side, regenerates the DBC, commits + pushes if changed, posts the diff comment. |
| [`tools/dbc_pr_comment.py`](tools/dbc_pr_comment.py) | Renders the wire-contract diff as PR-comment markdown. Parses both DBCs with cantools (the same view every consumer parses with), surfaces added / removed / changed messages and per-signal `length` / `byte_order` / `is_signed` / `scale` / `offset` / `unit` deltas. |
| [`tools/check_ids.py`](tools/check_ids.py) | Cross-repo collision audit. Given one or more DBCs, fails on: same can_id claimed by ≥2 senders, DLC mismatch on a shared can_id, signal-name reuse with inconsistent units. |
| [`tools/bus_load.py`](tools/bus_load.py) | Classical-CAN worst-case bus-load estimator. Reads `GenMsgCycleTime` per message, fails on > 80 % utilisation at 500 kbps. |
| [`.github/workflows/verify.yml`](.github/workflows/verify.yml) | Self-test that runs `check_ids` / `bus_load` / `dbc_pr_comment.py` against AMS's current `dev` DBC on every push and PR. |
| [`.github/workflows/fleet-audit.yml`](.github/workflows/fleet-audit.yml) | Nightly cron that pulls each ECU's `dev` DBC, runs the audit over the union, opens an issue on failure. |
| [`docs/CONSUMING_REPOS.md`](docs/CONSUMING_REPOS.md) | The 5-line workflow snippet a firmware repo drops in, plus prerequisites and the hands-off rule for the in-tree DBC. |
| [`docs/APP_SETUP.md`](docs/APP_SETUP.md) | One-time runbook for provisioning the `dbcinator` GitHub App in the org: permissions, secrets, install targets, verification PR, key rotation. |

## Quick start for a consumer repo

```yaml
# .github/workflows/dbc-bot.yml in the firmware repo
name: dbcinator
on:
  pull_request:
    branches: [main, dev]
permissions:
  contents: write
  pull-requests: write
jobs:
  regenerate-dbc:
    runs-on: ubuntu-latest
    steps:
      - uses: isc-fs/IFS08-DBCinator/actions/dbc-bot@v1.0.0
        with:
          app-id:      ${{ secrets.DBCINATOR_APP_ID }}
          private-key: ${{ secrets.DBCINATOR_PRIVATE_KEY }}
          dbc-path:    docs/dbc/ams.dbc
```

Prerequisites: the firmware repo needs the code-first DSL set up, a
CMake `dbc_dump` target, and the in-tree DBC committed. See
[`docs/CONSUMING_REPOS.md`](docs/CONSUMING_REPOS.md).

## History

This repo started as a spec-first generator (`spec/*.py` → `gen_*.py`
→ DBCs + C / C++ codecs). That model proved fragile: the Python spec
could drift from the firmware's hand-rolled encoders without anyone
noticing — exactly the bug class (`#234`, `#243` on the AMS side)
the project was meant to prevent. We pivoted to a code-first DSL
inside each firmware repo, with this repo demoted to bot
infrastructure that runs the per-repo `dbc_dump` and reconciles the
in-tree DBC after every PR. The spec-first generators were removed
in the v1.0.0 cut.
