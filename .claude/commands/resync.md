Re-sync your understanding against memory and reality before proceeding. Do not skip any step.

## Steps

1. **Memory check**
   - Read `MEMORY.md` (auto-loaded into context, but re-confirm the active plan pointer).
   - Read the ACTIVE plan file linked from MEMORY (currently `project_phase1_8_plan.md`).
   - Read `project_orchestrator_conventions.md` for any conventions relevant to upcoming work.

2. **Reality check**
   - Run `git status --short` and `git log --oneline -5`.
   - For the in-flight item in the plan, verify the files/dirs/state the plan claims exist actually do (use `ls`, `grep`, `Read` — whatever fits).
   - If the plan references infra (AR repos, GCS buckets, GCP resources), spot-check with `gcloud` for the ones relevant to the current item.

3. **Drift detection**
   - List any mismatches: claims in memory that don't match reality, or recent changes not yet captured in memory.
   - Distinguish "stale (just needs update)" from "suspicious (something happened we didn't expect)".

4. **Reconcile**
   - For stale entries: update the relevant memory files directly.
   - For suspicious drift: STOP and flag it back to the user with the specific mismatch before changing anything.

5. **Report** in 3 lines max:
   - "Currently on item X of plan Y."
   - "Plan and reality: match" OR "Drift: <one-line summary>".
   - "Memory updates: <list, or 'none'>".

## After resync

$ARGUMENTS

If `$ARGUMENTS` is empty, just stop after the report and wait for the next instruction. If `$ARGUMENTS` contains an instruction (e.g., "then start item 3a"), proceed with that instruction *after* completing the resync.
