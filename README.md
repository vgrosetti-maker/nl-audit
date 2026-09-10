# nl-audit

Auditing tools for a small web studio's GitHub account and published sites.
Every check compares an **observed state** against a **declared state** and exits non-zero on violation. Nothing is inferred.

Written in Python, no dependencies beyond the standard library and the `gh` CLI.

## Why this exists

A security gate that classifies findings into two buckets will silently pass everything that fits neither.

Ours split files into *"internal, must not be public"* (fail) and *"public by design"* (pass). A sales proposal page built for a prospect — a real local business that never asked for it — was neither. It sat in HTTP 200 on a production site for **44 days** while three automated checks reported green every time. The script never ignored the leak: it stamped it as legitimate and exited 0.

The fix was a third bucket (`TERCEIROS_DIR`) plus a positive authorization gate: third-party material only ships when an explicit marker says it may. The regression test uses the real case, with the business anonymized.

The general rule, if you take one thing from this repo: **a binary classifier pushes the unforeseen case into the "all good" bucket.** Before trusting a gate, ask which category it cannot name, and where that category lands.

## What's inside

| File | What it does |
|---|---|
| `audit_repos.py` | Account invariants across repos: dependency alerts on, default branch = declared production branch, `.gitignore` covers secret patterns, declared canonical URL answers 200 at the real host, CI workflow actually **ran** (not merely exists). |
| `check_exposure.py` | Crosses the repo's tracked files against what the deployed site actually serves. Three buckets: ours, public-by-design, third-party. |
| `scan_skills.py` | Scans an agent-skill collection for broken references, dead paths and machine-specific absolute paths. |
| `medir_uso.py` | Measures which installed skills/agents ever get invoked, from local session transcripts. |

## Usage

```bash
cp declaracoes.example.json declaracoes.json   # declare your account, then edit
gh auth login                                  # audit_repos.py reads through gh
python audit_repos.py                          # full declared scope
python audit_repos.py --recorte repo-a,repo-b  # subset of the declared scope
python check_exposure.py <url> <repo-path> [--all]
```

`declaracoes.json` is **data, never code**, and stays out of git: it names your repos. The auditor refuses to run without it rather than auditing an empty scope and printing green.

Two design choices worth stating, because they are the ones that failed before:

- **The scope is a decision, not the account.** A repo that is not declared shows up in the report as *outside the scope* — never as a pass.
- **Exceptions live in the data, with a written reason**, and are printed in the report. An exception waives exactly one accepted state for one repo; it never opens the whole repo. An exception that no longer matches any repo fails the run instead of dying in silence.

## Tests

```bash
python -m unittest discover -s tests
```

108 tests. Every invariant is tested from both ends: the injected defect **must** be caught, and the legitimate case **must not** be. A detector that has never accused anything proves nothing.

## CI

Two workflows, split on purpose by what they need:

- `tests.yml` runs the suite on every push and pull request. No secrets, so anyone who clones this repo gets the same result.
- `audit.yml` runs the auditor itself, daily at 06:00 UTC and on manual dispatch. It needs two account secrets (`GH_AUDIT_PAT`, `NL_AUDIT_DECLARACOES`) and fails loudly when they are missing, rather than reporting a green run over an empty scope.

A check that only runs on the author's laptop is a gate of honour, not a gate.

## Three outcomes, not one red/green

`audit.yml` classifies every run into exactly one of three buckets via
`classificar_desfecho.py` (unit-tested in `tests/test_classificar_desfecho.py`,
including the negative case: exit code 2 must never be reported as
`achado-real`):

- **`achado-real`** — the audited target itself is in violation. Fails the job
  (red), because this is the one case that should block a deploy.
- **`falha-de-credencial`** — a secret is missing or expired (`GH_AUDIT_PAT`,
  `NL_AUDIT_DECLARACOES`). This is the auditor's own infrastructure, not a
  finding about the target. It does **not** fail the job; it shows as a
  `::warning::` annotation and in the job summary, so it stays visible without
  lying that the target broke.
- **`falha-do-auditor`** — the job itself broke (dependency, syntax, timeout,
  any exit code `audit_repos.py` didn't itself classify). Same treatment as
  credential failure: visible, not a false failure of the target.

This repo absorbed `nl-audit-interno` (2026-09-10, archived, not deleted). That
repo was the original (started 2026-08-10) but was left behind: its own CI
never had `GH_AUDIT_PAT` configured on itself — the setup instructions in its
README pointed at setting the secret on *this* repo instead — so it failed
`falha-de-credencial` on all 10 of its last 10 runs while reporting flat
`failure`, indistinguishable from a real finding. Every feature it had
(`audit_repos.py`, `check_exposure.py`, `scan_skills.py`, tests) already existed
here in a more complete form (generalized paths, `declaracoes.json` externalized
as data, the `ci` invariant checking the actual conclusion instead of mere
execution), so nothing needed porting — only the workflow gained the
three-way triage above.

## License

MIT. See `LICENSE`.
