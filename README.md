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

## License

MIT. See `LICENSE`.
