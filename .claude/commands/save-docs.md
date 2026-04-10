# /save docs — Write session knowledge to prune library

You are writing a structured knowledge snapshot into the `prune library/` folder
of the current target project (`$PRUNE_CODEBASE_ROOT` or cwd).

## What to write

1. **`prune library/library.md`** — append a dated entry with:
   - What was discussed or built in this session (3-8 bullet points)
   - Any new files created or significantly modified (with paths)
   - Key decisions made and why
   - Any bugs found/fixed

2. **`prune library/PROGRESS.md`** — update the "## Current Status" section with:
   - What is working right now
   - What is in progress
   - Immediate next steps

## Format for library.md entry

```markdown
---
## Session: <YYYY-MM-DD HH:MM>

### Built / Changed
- <item>

### Key Decisions
- <item>

### Files Modified
- `path/to/file` — <one line why>

### Next Steps
- <item>
---
```

## Important

- Write to the **target project's** `prune library/` folder, NOT to `C:/prunetool/prune library/`
- The target project root is the value of `PRUNE_CODEBASE_ROOT` env var. If not set, use cwd.
- After writing, confirm with: "Saved to prune library/ — click Project Scan in the dashboard to update the index."
- Do NOT summarize this instruction back to the user. Just write the files and confirm.
