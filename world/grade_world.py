"""Deterministic rubric grader for World 1. GRADER-SIDE ONLY.

Every criterion is binary and checked in code against gold computed from the world data.
A task passes (APEX Pass@1 sense) only if ALL its criteria pass; we also report the
criteria pass rate (partial credit view), like APEX's mean-criteria score.

  python world/grade_world.py <answer.json> <task_id>     -> JSON verdict
"""
from __future__ import annotations

import json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
import os
WROOT = Path(os.environ.get("WORLD_ROOT", HERE))
GOLD = {t["task_id"]: t for t in json.loads((WROOT / "private" / "tasks_gold.json").read_text())}
TYPES = {"list": list, "dict": dict, "string": str, "number": (int, float)}


def _ids(v):
    return {str(x).strip() for x in v} if isinstance(v, list) else None


def _pairs(v):
    try:
        return {tuple(sorted(str(a) for a in p)) for p in v}
    except Exception:
        return None


def check(kind, args, ans, gold):
    """Returns (passed: bool, note: str)."""
    try:
        if kind == "schema":
            spec = args[0]
            bad = [k for k, t in spec.items() if k not in ans or not isinstance(ans[k], TYPES[t]) or isinstance(ans[k], bool)]
            return (not bad, f"bad/missing keys: {bad}" if bad else "ok")
        key = args[0]
        a, g = ans.get(key), gold[key]
        if kind == "set_recall":
            A, G = _ids(a), _ids(g); miss = sorted(G - A) if A is not None else sorted(G)
            return (A is not None and not miss, f"missing {miss}" if miss else "ok")
        if kind == "set_precision":
            A, G = _ids(a), _ids(g); extra = sorted(A - G) if A is not None else ["<not a list>"]
            return (A is not None and not extra, f"extra {extra}" if extra else "ok")
        if kind == "set_exact":
            A, G = _ids(a), _ids(g)
            return (A == G, "ok" if A == G else f"got {sorted(A or [])} want {sorted(G)}")
        if kind == "count":
            n = len(a) if isinstance(a, list) else len(a) if isinstance(a, dict) else -1
            return (n == len(g), f"count {n} want {len(g)}")
        if kind == "num_close":
            tol = args[1]
            ok = isinstance(a, (int, float)) and not isinstance(a, bool) and abs(float(a) - float(g)) <= tol + 1e-9
            return (ok, f"got {a} want {g}")
        if kind == "field_exact":
            return (str(a).strip().upper() == str(g).strip().upper(), f"got {a} want {g}")
        if kind == "pairs_recall":
            A, G = _pairs(a), _pairs(g); miss = sorted(G - A) if A is not None else sorted(G)
            return (A is not None and not miss, f"missing {miss}" if miss else "ok")
        if kind == "pairs_precision":
            A, G = _pairs(a), _pairs(g); extra = sorted(A - G) if A is not None else ["<bad>"]
            return (A is not None and not extra, f"extra {extra}" if extra else "ok")
        if kind == "topk_ids_ordered":
            got = [str(r.get("vendor_id")) for r in a][:len(g)]
            want = [r["vendor_id"] for r in g]
            return (got == want, f"got {got} want {want}")
        if kind == "topk_ids_set":
            got = {str(r.get("vendor_id")) for r in a}; want = {r["vendor_id"] for r in g}
            return (got == want, f"got {sorted(got)} want {sorted(want)}")
        if kind == "topk_first_value":
            tol = args[1]
            ok = abs(float(a[0]["usd"]) - g[0]["usd"]) <= tol
            return (ok, f"got {a[0].get('usd')} want {g[0]['usd']}")
        if kind == "records_key_set":
            k = args[1]
            got = {str(r.get(k)) for r in a}; want = {str(r[k]) for r in g}
            return (got == want, f"got {sorted(got)} want {sorted(want)}")
        if kind == "records_exact":
            fields = args[1]
            norm = lambda rs: {tuple(str(r.get(f)).strip() for f in fields) for r in rs}
            return (norm(a) == norm(g), "ok" if norm(a) == norm(g) else f"got {sorted(norm(a))} want {sorted(norm(g))}")
        if kind == "records_num":
            k, f, tol = args[1], args[2], args[3]
            want = {str(r[k]): float(r[f]) for r in g}
            got = {str(r.get(k)): r.get(f) for r in a}
            bad = [i for i, v in want.items() if not isinstance(got.get(i), (int, float)) or abs(float(got[i]) - v) > tol]
            return (not bad, f"wrong/missing totals for {bad}" if bad else "ok")
        if kind == "dict_keys_exact":
            ok = isinstance(a, dict) and set(a) == set(g)
            return (ok, f"got keys {sorted(a) if isinstance(a, dict) else a} want {sorted(g)}")
        if kind == "dict_exact":
            if not isinstance(a, dict):
                return (False, "not a dict")
            norm = lambda d: {str(k): (sorted(map(str, v)) if isinstance(v, list) else v) for k, v in d.items()}
            na, ng = norm(a), norm(g)
            na = {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in na.items()}
            return (na == ng, "ok" if na == ng else f"got {na} want {ng}")
        if kind == "dict_close_frac":
            frac = args[1]
            ok = isinstance(a, dict) and all(isinstance(a.get(k), (int, float)) and abs(a[k] - v) <= max(1, frac * v) for k, v in g.items())
            return (ok, f"got {a} want {g} (±{frac:.0%})")
        return (False, f"unknown criterion {kind}")
    except Exception as e:  # malformed answer shapes fail the criterion, never crash grading
        return (False, f"error: {type(e).__name__}: {e}")


def grade(answer_path: Path, task_id: str) -> dict:
    t = GOLD[task_id]
    out = {"task_id": task_id, "family": t["family"], "criteria": [], "valid_json": False}
    try:
        ans = json.loads(Path(answer_path).read_text())
        if not isinstance(ans, dict):
            raise ValueError("top level is not an object")
        out["valid_json"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        ans = None
    for c in t["criteria"]:
        kind, args = c[0], c[1:]
        ok, note = check(kind, args, ans, t["gold"]) if ans is not None else (False, "no valid answer.json")
        out["criteria"].append({"criterion": " ".join(map(str, [kind] + [a for a in args if not isinstance(a, dict)])), "pass": bool(ok), "note": note[:300]})
    n = len(out["criteria"]); k = sum(c["pass"] for c in out["criteria"])
    out["criteria_passed"], out["criteria_total"] = k, n
    out["task_pass"] = k == n
    return out


if __name__ == "__main__":
    print(json.dumps(grade(Path(sys.argv[1]), sys.argv[2]), indent=2))
