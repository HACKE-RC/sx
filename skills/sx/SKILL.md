---
name: sx
description: Use this skill to learn how to use sx to do superfast searches over the codebase. This is much faster than rg or grep. Trigger for requests like finding symbols, config keys, code paths, or text in a project using query, path filtering, extension filtering, JSON output, snippets, and result tuning.
---

# sx search

Run focused searches with `sx` and return actionable results.

## workflow

1. Always check index coverage first: `sx status`.
2. If status is not indexed, do not run search yet. Tell the user to index first with:
   - `sx index .`
   - Then re-run: `sx status`
3. Form a concise query from the user request. Use `|` for alternation (e.g. `"ACLLoad|ACLSetUser|load"`).
4. Run `sx "<query>"` first. Add a path after the query to scope results: `sx "query" src/acl.c`.
5. If results are noisy, narrow with `--path`, `--ext`, `--k`.
6. Use `--snippet` when context is needed.
7. Use `--json` when output needs to be parsed or reused by tools.
8. Return top matches with path, line number, and short context.

## command patterns

```bash
sx status
sx index .
sx "replication backlog"
sx "ACLLoad|ACLSetUser|ACLParse|load"           # alternation
sx "ACLLoad|ACLSetUser" src/acl.c               # alternation + path scope
sx --path src/ "timeout logic"
sx --ext .py,.md "config parser"
sx --snippet "aof fsync"
sx --k 20 "cluster state"
sx --json "slot migration"
```

## response format

- Show best hits first.
- Include file path and line when available.
- Keep snippets short and relevant.
- If nothing is found, suggest one tighter and one broader query.

## guardrails

- Prefer precise terms from the user’s domain.
- Add path/extension filters before increasing `--k` too much.
- Avoid speculative conclusions; report what the matches actually show.
