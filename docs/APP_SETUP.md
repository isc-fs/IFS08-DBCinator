# `dbcinator` GitHub App — one-time setup

The `dbcinator` bot needs a GitHub App identity. It can't run as a personal
access token (PAT) — those die with their owner and don't survive a graduate
leaving the team. This is the runbook for provisioning the App in the
`isc-fs` org so that workflows can mint short-lived install tokens from it.

Do this **once** per org. After that, every ECU repo just installs the App
and adds the workflow from `docs/CONSUMING_REPOS.md`.

> ⚠️ Org owner access required (steps 1–3 + 6). The rest is per-repo and
> can be delegated.

---

## 1. Create the App

Go to **`isc-fs` → Settings → Developer settings → GitHub Apps → New GitHub
App** and fill in:

| Field | Value |
|---|---|
| GitHub App name | `dbcinator` |
| Homepage URL | `https://github.com/isc-fs/IFS08-DBCinator` |
| Description | "Regenerates each firmware repo's DBC from its code-first DSL on every PR. See isc-fs/IFS08-DBCinator." |
| Callback URL | (leave blank — no OAuth flow) |
| Webhook → Active | **uncheck** (App is just an identity; workflows invoke it) |

**Permissions** (Repository permissions only):

| Permission | Access |
|---|---|
| Contents | **Read & write** |
| Pull requests | **Read & write** |
| Issues | Read & write (only needed for the nightly fleet audit; harmless otherwise) |
| Metadata | Read (default, can't be unset) |

**Where can this GitHub App be installed?** → "Only on this account".

Click **Create GitHub App**.

## 2. Note the App ID and generate the private key

Right after creation, GitHub shows:

- **App ID** — a 6-digit number. Note it (we'll store as a secret).
- A **Generate a private key** button at the bottom of the page. Click it. A
  `.pem` file downloads. **This is the App's credential — treat as a secret.**
  Store the contents (the entire PEM block including `-----BEGIN…-----` and
  `-----END…-----`) for step 3.

If the private key is ever exposed, revoke it on the App's settings page and
generate a new one. The org secret needs to be rotated in lockstep.

## 3. Add org-level secrets

Go to **`isc-fs` → Settings → Secrets and variables → Actions → New
organization secret** and add:

| Secret name | Value | Access |
|---|---|---|
| `DBCINATOR_APP_ID` | The App ID number from step 2 | Selected repositories (see step 4) |
| `DBCINATOR_PRIVATE_KEY` | The full PEM contents from step 2 | Selected repositories |

Restricting access to selected repos (rather than "All repositories") is the
safe default. Add a repo to the access list whenever it adopts the bot
(step 4 below).

## 4. Install the App on each ECU repo

On the App's settings page → **Install App** → `isc-fs` → choose **Only
select repositories** → add:

- `IFS08-CE-AMS`
- `IFS08-CE-ECU` (when ready)
- `IFS08-CE-UDV` (when ready)
- `IFS08-CE-DASH` (when ready)
- `IFS08-DBCinator` (this repo — for the nightly fleet audit)

Click **Install**.

Then go back to the org's Actions secrets (step 3) and add each of these
repos to the access list for both `DBCINATOR_APP_ID` and
`DBCINATOR_PRIVATE_KEY`.

## 5. Drop in the workflow

In each adopting repo, copy `.github/workflows/dbc-bot.yml` from
`docs/CONSUMING_REPOS.md`. Make sure the prerequisites (host-buildable
`dbc_dump` target, committed `docs/dbc/<name>.dbc`) are in place — see that
doc.

## 6. Verify on a no-op PR

Open a PR that touches a `.def` formatting comment (whitespace only). The
bot should:

- Run the `dbc-bot` workflow.
- Find no diff between regenerated and on-disk DBC.
- Post a single green "wire contract unchanged" comment.

Then open a PR that actually changes a signal (rename a field, flip an
endian). The bot should:

- Run the workflow.
- Commit the regenerated DBC onto the PR branch as `dbcinator[bot]`.
- Post a structured diff comment listing the change.

If both pass, the App is set up correctly.

## 7. Rotation runbook

If the private key ever leaks (or annually, whichever is sooner):

1. On the App's settings page, **Generate a private key** again.
2. **Update the `DBCINATOR_PRIVATE_KEY` org secret** with the new PEM.
3. On the App's settings page, find the old key in the list and **revoke**
   it.
4. The next workflow run will pick up the new credential automatically — no
   firmware-side change required.

Do not skip step 3. A revoked-but-still-valid key is the exact attack
surface the rotation closes.

## Quick reference

| Thing | Where |
|---|---|
| The App | `isc-fs/dbcinator` (org settings) |
| App ID secret | `DBCINATOR_APP_ID` (org secret) |
| Private key secret | `DBCINATOR_PRIVATE_KEY` (org secret) |
| Composite action | `isc-fs/IFS08-DBCinator/actions/dbc-bot` |
| Per-repo workflow | `.github/workflows/dbc-bot.yml` |
