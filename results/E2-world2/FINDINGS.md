# E.2 run 2 (fixed path) — the numbers, and two findings they exposed

Recorded: MINIMAL 17/25 (68%, Wilson 95% lower 48%), GATED 18/25 (72%, lower
52%). Exactly McNemar: 1 discordant (W2-004, GATED won, MINIMAL scored zero),
p = 1.0000. Verdict REJECT — rule version 1, fixed before results existed:
neither arm's 95% Wilson lower bound clears the 0.70 target at n=25, and the
weakest arm decides. That is a power statement, not a demonstration of
incapability: the point estimates sit at 68-72% and the bound spans ~±20.

No voids (0 of 50 rc=124). Receipts chain: OK (51 receipts).

## Finding 1 — the completion gate cannot pass on the bench path

All 25 GATED attempts carry
`stop/completion: completion: deliverable <sandbox>/deliverable.json does not exist`,
yet 18 of them still answer correctly. The arm delivers its answer through the
reply (`ANSWER: <json>`); nothing in minimal_arm ever writes deliverable.json,
and the gate checks for that file. A gate that can never pass is decoration,
and this one pollutes every GATED row's gate_blocks. Fixed separately; it did
not change any recorded answer (the answers stand with the block attached).

## Finding 2 — the transform family fails identically on both arms

W2-003/008/013/018/023 (command-output-transformation) score 0/10 across arms
with stopped_reason=unparseable_reply. Live probe: the model replies to those
tasks with a JSON tool-call shape — `{"tool": "read", "path":
"world_files/W2-003.sh"}` — which neither _extract_command (needs a whitelisted
shell prefix) nor _extract_answer (needs an ANSWER: line) recognizes. Both
arms share the extractors, so both fail identically: this is an
instruction-following failure of the model on this family's prompt, not an arm
difference. Recorded, not papered over.

## What is NOT claimed

No capability claim either way beyond these 50 attempts on one 25-task world
with one model. The verdict is REJECT by the pre-registered rule.
