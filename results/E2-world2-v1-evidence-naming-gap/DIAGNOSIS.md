# E.2 run 1 — 0/25 both arms, and why (a harness defect, diagnosed and fixed)

Recorded result: MINIMAL 0/25, GATED 0/25, 19-20 empty answers each.
Verdict as recorded: INCONCLUSIVE (empty-answer rate > 20%).

Diagnosis (live, with the relay log clearing the network — 627 calls, 625 HTTP
200, one 429 absorbed, retries doing real work): the arm catted the CORRECT
evidence file at call 2 (W2-000.conf for task W2-000 — it inferred the naming
convention), saw the TRUE value (secret_key_99 = value_2022), and then never
committed: it kept reading W2-005, W2-010, W2-015 ... until its attempts ran
out. Nothing in its prompt ever confirmed which file was ITS task's file, so
the conflicting values it found could not be resolved and no answer was safe.

Root cause is this repo's, not the model's: run_world.py appends "your task's
evidence file is world_files/<name>" to the description it hands the engine,
but operator_mode passed the RAW description to the bench arms — the
manifest's evidence_file field never reached them. One runner had the plan's
design wired; the other did not.

Proof it was the wiring: with the naming delivered to the arm, the same arm,
the same model, the same task answers {"value": "value_2022"} in 3 calls,
stopped_reason "answered".

This run is kept as the record of the condition that produced it. The fix is
committed separately; the re-run lands next to this directory.
