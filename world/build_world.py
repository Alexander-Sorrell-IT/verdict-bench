"""World 1: "Halvorsen Freight - Ops & Audit Desk". APEX-style, deterministic gold.

One seeded generator writes a whole company's files (ledger, bank, vendors, contracts,
invoices, auth/VPN logs, HR, service configs, policies, tickets, expenses), plants known
anomalies, and computes every task's gold answer FROM THE SAME DATA in code. Each task gets
3-5 binary criteria that a pure checker grades (no LLM judge needed for any of them).

  python world/build_world.py            -> world/public/   (what agents see: the files)
                                            world/private/  (tasks gold + criteria; grader only)
"""
from __future__ import annotations

import csv, datetime as dt, hashlib, json, random, shutil, statistics
from collections import defaultdict
from pathlib import Path

import os
SEED = int(os.environ.get("WORLD_SEED", "20261120"))
HERE = Path(os.environ.get("WORLD_DIR", Path(__file__).resolve().parent))
PUB, PRIV = HERE / "public", HERE / "private"
R = random.Random(SEED)
D0 = dt.date(2026, 7, 1)
Q_END = dt.date(2026, 9, 30)


def day(i):  # 0..91 -> date in Q3
    return D0 + dt.timedelta(days=i)


def wcsv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


# ------------------------------------------------------------------ people & vendors
DEPTS = ["Finance", "Ops", "Engineering", "Sales", "Security", "HR"]
FIRST = ["ana", "ben", "chen", "dara", "eli", "fay", "gus", "hana", "ivo", "jun", "kai", "lena",
         "milo", "nia", "omar", "pia", "quin", "rosa", "sami", "tess", "uma", "vik", "wren", "xan",
         "yara", "zed", "ari", "bo", "cy", "dee", "enzo", "flo", "gia", "hal", "ida", "joe"]
LAST = ["berg", "cole", "diaz", "ekstr", "frost", "gray", "holm", "ivers", "jansen", "kerr",
        "lund", "moss", "nash", "ortiz", "park", "quist", "reyes", "stone"]


def build_people():
    emps, used = [], set()
    for i in range(60):
        while True:
            u = f"{R.choice(FIRST)}.{R.choice(LAST)}"
            if u not in used:
                used.add(u); break
        hired = dt.date(2019, 1, 1) + dt.timedelta(days=R.randint(0, 2500))
        emps.append({"emp_id": f"E{1000+i}", "username": u, "dept": DEPTS[i % len(DEPTS)],
                     "hire_date": hired, "status": "active", "termination_date": None})
    # 8 terminations inside Q3, 3 before Q3
    for e in R.sample(emps, 11):
        e["status"] = "terminated"
        e["termination_date"] = day(R.randint(5, 80)) if len([x for x in emps if x["termination_date"] and x["termination_date"] >= D0]) < 8 else dt.date(2026, R.randint(2, 6), R.randint(1, 28))
    # 4 hires during Q3 (after 2026-07-01), one of them after Q_END is impossible -> keep in Q3
    for e in R.sample([x for x in emps if x["status"] == "active"], 4):
        e["hire_date"] = day(R.randint(10, 85))
    return emps


COUNTRIES = ["US", "US", "US", "DE", "GB", "NO", "MX", "CA"]
CURR = {"US": "USD", "DE": "EUR", "GB": "GBP", "NO": "NOK", "MX": "MXN", "CA": "CAD"}
SERVICES = ["freight-haul", "warehousing", "customs-brokerage", "fuel-surcharge", "it-support",
            "cleaning", "security-guard", "consulting", "software-license", "fleet-maintenance"]


def build_vendors():
    vs = []
    for i in range(1, 25):
        c = R.choice(COUNTRIES)
        approved = sorted(R.sample(SERVICES, R.randint(2, 4)))
        caps = {s: round(R.uniform(40, 400), 2) for s in approved}
        start = dt.date(2025, R.randint(1, 12), R.randint(1, 28))
        expiry = dt.date(2027, R.randint(1, 12), 28)
        vs.append({"vendor_id": f"V{i:03d}", "name": f"{R.choice(['Nord','Atlas','Fjord','Summit','Harbor','Pioneer','Keystone','Granite'])} {R.choice(['Logistics','Services','Supply','Partners','Systems','Group'])} {i}",
                   "country": c, "currency": CURR[c], "status": "active",
                   "terms_days": R.choice([15, 30, 45, 60]), "approved": approved, "caps": caps,
                   "start": start, "expiry": expiry})
    # planted: 3 contracts expire mid-quarter, 2 vendors inactive in master
    for v in R.sample(vs, 3):
        v["expiry"] = day(R.randint(20, 60))
    for v in R.sample([v for v in vs if v["expiry"] > Q_END], 2):
        v["status"] = "inactive"
    return vs


FX = {"USD": 1.0, "EUR": 1.0841, "GBP": 1.2712, "NOK": 0.0933, "MXN": 0.0541, "CAD": 0.7302}


# ------------------------------------------------------------------ finance
def build_finance(vendors):
    gl, bank = [], []
    eid, bid = 5000, 90000
    for _ in range(260):
        v = R.choice(vendors)
        d = day(R.randint(0, 88))
        amt = round(R.uniform(150, 9000), 2)
        eid += 1
        gl.append({"entry_id": f"GL{eid}", "date": d, "account": R.choice(["6100", "6200", "6300", "6400"]),
                   "vendor_id": v["vendor_id"], "currency": v["currency"], "amount": amt,
                   "memo": f"payment {v['name']}"})
        bid += 1
        bank.append({"bank_ref": f"BK{bid}", "date": d + dt.timedelta(days=R.randint(0, 2)),
                     "currency": v["currency"], "amount": amt, "description": f"ACH {v['name'][:14].upper()}"})
    # planted: 7 GL entries with no bank line (remove their bank twin)
    gl_only = R.sample(gl, 7)
    drop = {g["entry_id"] for g in gl_only}
    pairs = list(zip(gl, bank))
    bank = [b for g, b in pairs if g["entry_id"] not in drop]
    # planted: 6 bank-only lines (fees / unknown debits)
    bank_only = []
    for _ in range(6):
        bid += 1
        b = {"bank_ref": f"BK{bid}", "date": day(R.randint(0, 88)), "currency": "USD",
             "amount": round(R.uniform(20, 2500), 2), "description": R.choice(["SVC FEE", "WIRE OUT 4471", "CARD SETTLE", "UNKNOWN DEBIT"])}
        bank.append(b); bank_only.append(b)
    # planted: 5 duplicate payments (same vendor+amount, 1-6 days apart), each duplicate also hits bank
    dups = []
    for g in R.sample([g for g in gl if g["entry_id"] not in drop], 5):
        eid += 1; bid += 1
        d2 = g["date"] + dt.timedelta(days=R.randint(1, 6))
        g2 = dict(g, entry_id=f"GL{eid}", date=d2, memo=g["memo"] + " (re-run)")
        gl.append(g2)
        bank.append({"bank_ref": f"BK{bid}", "date": d2, "currency": g["currency"], "amount": g["amount"],
                     "description": f"ACH DUP {g['vendor_id']}"})
        dups.append(sorted([g["entry_id"], g2["entry_id"]]))
    # planted: 4 GL entries posted in October for September bank activity (cutoff errors)
    cutoff = []
    for g in R.sample([g for g in gl if g["entry_id"] not in drop and dt.date(2026, 9, 20) <= g["date"] <= dt.date(2026, 9, 28)], 4) if False else []:
        pass
    sept = [g for g in gl if g["entry_id"] not in drop and g["date"].month == 9 and g["date"].day <= 26
            and not any(g["entry_id"] in p for p in dups)]
    for g in R.sample(sept, 4):
        g["posted"] = dt.date(2026, 10, R.randint(1, 6)); cutoff.append(g["entry_id"])
    for g in gl:
        g.setdefault("posted", g["date"] + dt.timedelta(days=R.randint(0, 3)) if g["date"] <= dt.date(2026, 9, 26) else g["date"])
        if g["posted"] > Q_END and g["entry_id"] not in cutoff:
            g["posted"] = Q_END
    R.shuffle(bank)
    gl.sort(key=lambda g: (g["date"], g["entry_id"]))
    return gl, bank, drop, bank_only, dups, cutoff


def match_gl_bank(gl, bank):
    """Reference matcher for the gold: same currency+amount, bank date within 0..3 days, 1:1."""
    used, unmatched = set(), []
    pool = defaultdict(list)
    for b in bank:
        pool[(b["currency"], b["amount"])].append(b)
    for g in sorted(gl, key=lambda g: (g["date"], g["entry_id"])):
        cands = [b for b in pool[(g["currency"], g["amount"])] if b["bank_ref"] not in used
                 and 0 <= (b["date"] - g["date"]).days <= 3]
        if cands:
            used.add(min(cands, key=lambda b: (b["date"], b["bank_ref"]))["bank_ref"])
        else:
            unmatched.append(g["entry_id"])
    return unmatched, [b["bank_ref"] for b in bank if b["bank_ref"] not in used]


# ------------------------------------------------------------------ invoices
def build_invoices(vendors):
    invs, n = [], 3000
    flags = defaultdict(list)
    for _ in range(70):
        v = R.choice(vendors)
        n += 1
        d = day(R.randint(0, 88))
        lines = []
        for s in R.sample(v["approved"], min(len(v["approved"]), R.randint(1, 3))):
            lines.append({"service": s, "qty": R.randint(1, 20), "unit_price": round(v["caps"][s] * R.uniform(0.6, 1.0), 2)})
        invs.append({"invoice_id": f"INV-{n}", "vendor_id": v["vendor_id"], "date": d, "currency": v["currency"], "lines": lines})
    pick = lambda k, cond=lambda i: True: R.sample([i for i in invs if cond(i)], k)
    vmap = {v["vendor_id"]: v for v in vendors}
    # rate-cap breaches
    for i in pick(6):
        ln = R.choice(i["lines"]); ln["unit_price"] = round(vmap[i["vendor_id"]]["caps"][ln["service"]] * R.uniform(1.08, 1.4), 2)
    # unapproved services
    for i in pick(5):
        v = vmap[i["vendor_id"]]
        other = [s for s in SERVICES if s not in v["approved"]]
        i["lines"].append({"service": R.choice(other), "qty": R.randint(1, 5), "unit_price": round(R.uniform(50, 300), 2)})
    # invoices after contract expiry
    expiring = [v["vendor_id"] for v in vendors if v["expiry"] <= Q_END]
    for vid in expiring:
        v = vmap[vid]
        for _ in range(2):
            n += 1
            s = v["approved"][0]
            invs.append({"invoice_id": f"INV-{n}", "vendor_id": vid, "date": v["expiry"] + dt.timedelta(days=R.randint(1, 20)),
                         "currency": v["currency"], "lines": [{"service": s, "qty": R.randint(1, 9), "unit_price": round(v["caps"][s] * 0.9, 2)}]})
    for i in invs:
        i["total"] = round(sum(l["qty"] * l["unit_price"] for l in i["lines"]), 2)
    # arithmetic errors in stated total
    for i in pick(5):
        i["total"] = round(i["total"] + R.choice([-1, 1]) * R.uniform(15, 400), 2)
    return invs


# ------------------------------------------------------------------ logs
def build_logs(emps):
    users = [e["username"] for e in emps]
    lines, vpn = [], []
    hosts = ["bastion-1", "bastion-2", "db-admin", "ci-runner"]
    def ts(d, h, m, s):
        return dt.datetime.combine(d, dt.time(h, m, s))
    for _ in range(1800):
        u = R.choice(users); d = day(R.randint(0, 29) + 62)  # September
        t = ts(d, R.randint(7, 19), R.randint(0, 59), R.randint(0, 59))
        ok = R.random() > 0.08
        lines.append((t, R.choice(hosts), u, f"10.20.{R.randint(0,40)}.{R.randint(2,250)}", "Accepted" if ok else "Failed"))
    # brute force: 5 external IPs, 12-30 failures within 4 minutes; 2 of them then succeed
    brute, compromised = [], []
    for k in range(5):
        ip = f"185.{R.randint(10,250)}.{R.randint(0,255)}.{R.randint(2,250)}"; brute.append(ip)
        d = day(62 + R.randint(0, 29)); base = ts(d, R.randint(0, 23), R.randint(0, 50), 0)
        tgt = R.choice(users)
        for j in range(R.randint(12, 30)):
            lines.append((base + dt.timedelta(seconds=R.randint(0, 230)), "bastion-1", R.choice([tgt, "root", "admin"]), ip, "Failed"))
        if k < 2:
            okt = base + dt.timedelta(seconds=300 + R.randint(0, 600))
            lines.append((okt, "bastion-1", tgt, ip, "Accepted"))
            compromised.append({"ip": ip, "user": tgt, "time": okt.strftime("%Y-%m-%dT%H:%M:%S")})
    # decoy: an IP with 9 failures in 5 min (below threshold) and one with 15 spread over 3 hours
    ip = "203.0.113.77"; base = ts(day(70), 3, 0, 0)
    for j in range(9):
        lines.append((base + dt.timedelta(seconds=j * 25), "bastion-2", "root", ip, "Failed"))
    ip = "203.0.113.90"; base = ts(day(75), 1, 0, 0)
    for j in range(15):
        lines.append((base + dt.timedelta(minutes=j * 12), "bastion-2", "admin", ip, "Failed"))
    # logins by terminated employees after their termination date
    term = [e for e in emps if e["termination_date"] and e["termination_date"] >= dt.date(2026, 9, 1)]
    term_after = []
    for e in term[:3]:
        d = e["termination_date"] + dt.timedelta(days=R.randint(1, 5))
        if d <= Q_END:
            lines.append((ts(d, 10, R.randint(0, 59), 1), "db-admin", e["username"], "10.20.3.3", "Accepted"))
            term_after.append(e["username"])
    # remove ordinary logins by terminated users after termination so only planted ones remain
    tmap = {e["username"]: e["termination_date"] for e in emps if e["termination_date"]}
    planted_t = {(u) for u in term_after}
    clean = []
    for l in lines:
        t, h, u, ip_, res = l
        if u in tmap and t.date() > tmap[u] and res == "Accepted" and not (u in planted_t and h == "db-admin" and ip_ == "10.20.3.3"):
            continue
        clean.append(l)
    lines = sorted(clean)
    # VPN: country per session; impossible travel = 2 countries < 2h apart
    travel = []
    for _ in range(500):
        u = R.choice(users); d = day(62 + R.randint(0, 29))
        vpn.append((ts(d, R.randint(6, 20), R.randint(0, 59), 0), u, "US", f"198.51.100.{R.randint(2,250)}"))
    for u in R.sample(users, 4):
        d = day(62 + R.randint(0, 29)); t1 = ts(d, R.randint(8, 12), 0, 0)
        vpn.append((t1, u, "US", "198.51.100.9"))
        vpn.append((t1 + dt.timedelta(minutes=R.randint(20, 100)), u, R.choice(["RU", "BR", "VN", "NG"]), f"45.{R.randint(1,250)}.{R.randint(1,250)}.9"))
        travel.append(u)
    # decoy: legit travel with 9h gap
    u = R.choice([x for x in users if x not in travel]); d = day(80)
    vpn.append((ts(d, 6, 0, 0), u, "US", "198.51.100.10")); vpn.append((ts(d, 17, 0, 0), u, "GB", "81.2.69.10"))
    vpn.sort()
    return lines, vpn, brute, compromised, term_after, travel


# ------------------------------------------------------------------ configs
def build_configs(emps):
    active = [e["username"] for e in emps if e["status"] == "active"]
    inactive = [e["username"] for e in emps if e["status"] == "terminated"]
    svcs = {}
    for name in ["billing-api", "tms-core", "driver-app", "edi-gateway", "warehouse-scan", "hr-portal",
                 "reporting", "fuel-cards", "customs-sync", "auth-proxy", "doc-store", "fleet-telemetry"]:
        svcs[name] = {"service": name, "owner_team": R.choice(DEPTS),
                      "tls": {"min_version": R.choice(["1.2", "1.3"])},
                      "ports": [{"port": 443, "exposure": "public"}, {"port": R.choice([8080, 9090, 5432]), "exposure": "internal"}],
                      "logging": {"retention_days": R.choice([90, 180, 365])},
                      "admins": sorted(R.sample(active, 2)), "root_login": False}
    names = sorted(svcs)
    for n in R.sample(names, 3): svcs[n]["tls"]["min_version"] = R.choice(["1.0", "1.1"])
    for n in R.sample(names, 3): svcs[n]["logging"]["retention_days"] = R.choice([7, 30, 60])
    for n in R.sample(names, 2): svcs[n]["ports"].append({"port": 22, "exposure": "public"})
    svcs[R.choice(names)]["ports"].append({"port": 23, "exposure": "public"})
    svcs[R.choice(names)]["ports"].append({"port": 22, "exposure": "internal"})  # decoy: internal ssh is fine
    for n in R.sample(names, 3): svcs[n]["admins"] = sorted(svcs[n]["admins"] + [R.choice(inactive)])
    svcs[R.choice(names)]["admins"].append("contractor.temp")   # not in HR at all
    for n in R.sample(names, 2): svcs[n]["root_login"] = True
    return svcs


def yaml_dump(d, ind=0):
    out, p = [], "  " * ind
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f"{p}{k}:"); out.append(yaml_dump(v, ind + 1))
        elif isinstance(v, list):
            out.append(f"{p}{k}:")
            for it in v:
                if isinstance(it, dict):
                    items = list(it.items())
                    out.append(f"{p}  - {items[0][0]}: {json.dumps(items[0][1]) if isinstance(items[0][1], str) else items[0][1]}")
                    for kk, vv in items[1:]:
                        out.append(f"{p}    {kk}: {json.dumps(vv) if isinstance(vv, str) else vv}")
                else:
                    out.append(f"{p}  - {it}")
        elif isinstance(v, bool):
            out.append(f"{p}{k}: {'true' if v else 'false'}")
        elif isinstance(v, str):
            out.append(f'{p}{k}: "{v}"')
        else:
            out.append(f"{p}{k}: {v}")
    return "\n".join(out)


# ------------------------------------------------------------------ ops data
def build_ops(emps):
    tickets = []
    sla = {"P1": 4, "P2": 24, "P3": 72}
    for i in range(240):
        pr = R.choices(["P1", "P2", "P3"], [1, 3, 6])[0]
        opened = dt.datetime.combine(day(R.randint(0, 88)), dt.time(R.randint(0, 23), R.randint(0, 59)))
        hrs = R.uniform(0.3, sla[pr] * (2.2 if R.random() < 0.18 else 0.95))
        closed = opened + dt.timedelta(hours=hrs)
        tickets.append({"ticket_id": f"T{7000+i}", "priority": pr, "team": R.choice(["Ops", "Engineering", "Security", "Finance"]),
                        "opened": opened, "closed": closed if R.random() > 0.05 else None})
    expenses = []
    limits = {"meals": 75, "lodging": 250, "airfare": 900, "ground": 120, "supplies": 200}
    for i in range(200):
        cat = R.choice(list(limits))
        over = R.random() < 0.15
        amt = round(limits[cat] * (R.uniform(1.05, 2.5) if over else R.uniform(0.2, 0.98)), 2)
        approved = (R.random() < 0.5) if over else False
        expenses.append({"expense_id": f"X{4000+i}", "emp_id": R.choice(emps)["emp_id"], "date": day(R.randint(0, 88)),
                         "category": cat, "amount_usd": amt, "manager_approval": "yes" if approved else "no"})
    return tickets, expenses, sla, limits


# ------------------------------------------------------------------ write world + gold
def main():
    for p in (PUB, PRIV):
        if p.exists():
            shutil.rmtree(p)
    W = PUB / "world"
    emps = build_people(); vendors = build_vendors()
    gl, bank, gl_only_planted, bank_only_planted, dups, cutoff = build_finance(vendors)
    invs = build_invoices(vendors)
    auth, vpn, brute, compromised, term_after, travel = build_logs(emps)
    svcs = build_configs(emps)
    tickets, expenses, sla, limits = build_ops(emps)

    # ---- files
    wcsv(W / "hr/employees.csv", ["emp_id", "username", "dept", "hire_date", "status", "termination_date"],
         [[e["emp_id"], e["username"], e["dept"], e["hire_date"], e["status"], e["termination_date"] or ""] for e in emps])
    master = [v for v in vendors]
    wcsv(W / "vendors/vendor_master.csv", ["vendor_id", "name", "country", "currency", "status", "payment_terms_days"],
         [[v["vendor_id"], v["name"], v["country"], v["currency"], v["status"], v["terms_days"]] for v in master if v["vendor_id"] != "V024"])
    for v in vendors:
        body = [f"# Master Services Agreement - {v['name']} ({v['vendor_id']})", "",
                f"Effective date: {v['start']}  ", f"Expiry date: {v['expiry']}  ",
                f"Billing currency: {v['currency']}", "", "## Approved services and rate caps", "",
                "Unit prices invoiced under this agreement may not exceed the following caps (per unit, billing currency):", ""]
        body += [f"- {s}: {v['caps'][s]:.2f}" for s in v["approved"]]
        body += ["", "## Terms", "", "Services not listed above are out of scope and require a signed change order.",
                 "Invoices dated after the expiry date are not payable under this agreement.", ""]
        (W / "vendors/contracts").mkdir(parents=True, exist_ok=True)
        (W / f"vendors/contracts/{v['vendor_id']}.md").write_text("\n".join(body))
    wcsv(W / "finance/general_ledger_2026Q3.csv", ["entry_id", "txn_date", "posted_date", "account", "vendor_id", "currency", "amount", "memo"],
         [[g["entry_id"], g["date"], g["posted"], g["account"], g["vendor_id"], g["currency"], f"{g['amount']:.2f}", g["memo"]] for g in gl])
    wcsv(W / "finance/bank_statement_2026Q3.csv", ["bank_ref", "value_date", "currency", "amount", "description"],
         [[b["bank_ref"], b["date"], b["currency"], f"{b['amount']:.2f}", b["description"]] for b in bank])
    wcsv(W / "finance/fx_rates_q3_avg.csv", ["currency", "usd_per_unit"], [[c, r] for c, r in FX.items()])
    (W / "invoices").mkdir(parents=True, exist_ok=True)
    for i in invs:
        (W / f"invoices/{i['invoice_id']}.json").write_text(json.dumps(dict(i, date=str(i["date"])), indent=2))
    (W / "logs").mkdir(parents=True, exist_ok=True)
    (W / "logs/auth_2026-09.log").write_text("".join(
        f"{t:%Y-%m-%dT%H:%M:%S} {h} sshd: {res} password for {u} from {ip} port {R.randint(30000,65000)}\n" for t, h, u, ip, res in auth))
    wcsv(W / "logs/vpn_sessions_2026-09.csv", ["session_start", "username", "geo_country", "client_ip"],
         [[f"{t:%Y-%m-%dT%H:%M:%S}", u, c, ip] for t, u, c, ip in vpn])
    for n, s in svcs.items():
        (W / "configs/services").mkdir(parents=True, exist_ok=True)
        (W / f"configs/services/{n}.yaml").write_text(yaml_dump(s) + "\n")
    (W / "policy").mkdir(parents=True, exist_ok=True)
    (W / "policy/security_baseline.md").write_text("""# Security Baseline v4 (effective 2026-01-01)

SB-1  TLS: every service must set tls.min_version to 1.2 or higher.
SB-2  Remote shell: ports 22 (SSH) and 23 (Telnet) must never have exposure "public". Internal exposure is allowed.
SB-3  Log retention: logging.retention_days must be at least 90.
SB-4  Admins: every entry in admins must be the username of an ACTIVE employee in hr/employees.csv.
SB-5  root_login must be false.
""")
    (W / "policy/expense_policy.md").write_text("# Expense Policy\n\nPer-item limits (USD): " + ", ".join(f"{k} {v}" for k, v in limits.items()) +
        ".\nAny expense above its category limit requires manager_approval = yes. Over-limit items without approval are violations.\n")
    (W / "policy/sla_policy.md").write_text("# Support SLA\n\nResolution targets from opened to closed: " + ", ".join(f"{k} {v}h" for k, v in sla.items()) +
        ".\nA ticket breaches if (closed - opened) exceeds its target. Tickets still open at 2026-09-30T23:59:59 breach if that time minus opened exceeds the target.\n")
    wcsv(W / "ops/tickets_2026Q3.csv", ["ticket_id", "priority", "team", "opened", "closed"],
         [[t["ticket_id"], t["priority"], t["team"], f"{t['opened']:%Y-%m-%dT%H:%M}", f"{t['closed']:%Y-%m-%dT%H:%M}" if t["closed"] else ""] for t in tickets])
    wcsv(W / "finance/expenses_2026Q3.csv", ["expense_id", "emp_id", "date", "category", "amount_usd", "manager_approval"],
         [[x["expense_id"], x["emp_id"], x["date"], x["category"], f"{x['amount_usd']:.2f}", x["manager_approval"]] for x in expenses])
    (W / "README.md").write_text("""# Halvorsen Freight AS - shared drive export (Q3 2026)

finance/   general ledger, bank statement, FX averages, expense claims
vendors/   vendor master and one MSA per vendor (vendors/contracts/<vendor_id>.md)
invoices/  one JSON per vendor invoice
logs/      SSH auth log and VPN sessions for September 2026
hr/        employee roster
configs/   one YAML per production service
policy/    security baseline, expense policy, support SLA
ops/       support tickets
""")

    # ---- gold (computed from the data, never hand-typed)
    vmap = {v["vendor_id"]: v for v in vendors}
    master_ids = {v["vendor_id"]: v["status"] for v in vendors if v["vendor_id"] != "V024"}
    unmatched_gl, unmatched_bank = match_gl_bank(gl, bank)
    bank_by = {b["bank_ref"]: b for b in bank}
    G = {}
    G["R1"] = {"unmatched_entry_ids": sorted(unmatched_gl)}
    G["R2"] = {"bank_only_refs": sorted(unmatched_bank), "total_usd": round(sum(bank_by[r]["amount"] * FX[bank_by[r]["currency"]] for r in unmatched_bank), 2)}
    G["R3"] = {"duplicate_pairs": sorted(dups)}
    spend = defaultdict(float)
    for g in gl:
        spend[g["vendor_id"]] += g["amount"] * FX[g["currency"]]
    top = sorted(spend.items(), key=lambda kv: -kv[1])[:5]
    G["R4"] = {"top5": [{"vendor_id": k, "usd": round(v, 2)} for k, v in top]}
    G["R5"] = {"late_posted_entry_ids": sorted(g["entry_id"] for g in gl if g["date"] <= Q_END < g["posted"])}
    # logs
    fails = defaultdict(list)
    for t, h, u, ip, res in auth:
        if res == "Failed":
            fails[ip].append(t)
    def is_brute(ts_):
        ts_ = sorted(ts_)
        return any(sum(1 for x in ts_[i:] if (x - ts_[i]).total_seconds() <= 300) >= 10 for i in range(len(ts_)))
    brute_ips = sorted(ip for ip, ts_ in fails.items() if is_brute(ts_))
    G["L1"] = {"ips": brute_ips}
    comp = []
    for ip in brute_ips:
        first_fail = min(fails[ip])
        for t, h, u, ip2, res in auth:
            if ip2 == ip and res == "Accepted" and t > first_fail:
                comp.append({"ip": ip, "user": u, "time": f"{t:%Y-%m-%dT%H:%M:%S}"}); break
    G["L2"] = {"compromised": sorted(comp, key=lambda c: c["ip"])}
    tmap = {e["username"]: e["termination_date"] for e in emps if e["termination_date"]}
    G["L3"] = {"usernames": sorted({u for t, h, u, ip, res in auth if res == "Accepted" and u in tmap and t.date() > tmap[u]})}
    by_user = defaultdict(list)
    for t, u, c, ip in vpn:
        by_user[u].append((t, c))
    imp = set()
    for u, ss in by_user.items():
        ss.sort()
        for (t1, c1), (t2, c2) in zip(ss, ss[1:]):
            if c1 != c2 and (t2 - t1).total_seconds() < 7200:
                imp.add(u)
    G["L4"] = {"usernames": sorted(imp)}
    off = defaultdict(int)
    for t, h, u, ip, res in auth:
        if res == "Accepted" and h == "db-admin" and (t.hour >= 22 or t.hour < 6):
            off[u] += 1
    G["L5"] = {"accepted_db_admin_logins": sum(1 for t, h, u, ip, res in auth if res == "Accepted" and h == "db-admin"),
               "failed_total": sum(1 for l in auth if l[4] == "Failed")}
    # contracts / invoices
    over, unappr, expired, arith = [], [], [], []
    overbill = defaultdict(float)
    for i in invs:
        v = vmap[i["vendor_id"]]
        for l in i["lines"]:
            if l["service"] in v["caps"] and l["unit_price"] > v["caps"][l["service"]] + 1e-9:
                over.append(i["invoice_id"]); overbill[i["vendor_id"]] += (l["unit_price"] - v["caps"][l["service"]]) * l["qty"]
            if l["service"] not in v["approved"]:
                unappr.append(i["invoice_id"])
        if i["date"] > v["expiry"]:
            expired.append(i["invoice_id"])
        true = round(sum(l["qty"] * l["unit_price"] for l in i["lines"]), 2)
        if abs(true - i["total"]) > 0.005:
            arith.append({"invoice_id": i["invoice_id"], "correct_total": true})
    G["C1"] = {"invoice_ids": sorted(set(over))}
    G["C2"] = {"invoice_ids": sorted(set(expired))}
    G["C3"] = {"errors": sorted(arith, key=lambda a: a["invoice_id"])}
    G["C4"] = {"invoice_ids": sorted(set(unappr))}
    worst = max(overbill.items(), key=lambda kv: kv[1])
    G["C5"] = {"vendor_id": worst[0], "overbilled_amount": round(worst[1], 2), "currency": vmap[worst[0]]["currency"]}
    # configs
    active = {e["username"] for e in emps if e["status"] == "active"}
    viol = defaultdict(list)
    for n, s in sorted(svcs.items()):
        if float(s["tls"]["min_version"]) < 1.2: viol["SB-1"].append(n)
        if any(p["port"] in (22, 23) and p["exposure"] == "public" for p in s["ports"]): viol["SB-2"].append(n)
        if s["logging"]["retention_days"] < 90: viol["SB-3"].append(n)
        if any(a not in active for a in s["admins"]): viol["SB-4"].append(n)
        if s["root_login"]: viol["SB-5"].append(n)
    G["K1"] = {"services": sorted(viol["SB-1"])}
    G["K2"] = {"bad_admins": {n: sorted(a for a in s["admins"] if a not in active) for n, s in sorted(svcs.items()) if any(a not in active for a in s["admins"])}}
    G["K3"] = {"services": sorted(viol["SB-3"])}
    G["K4"] = {"services": sorted(viol["SB-2"])}
    G["K5"] = {"violations_by_rule": {r: len(viol[r]) for r in ["SB-1", "SB-2", "SB-3", "SB-4", "SB-5"]},
               "clean_services": sorted(n for n in svcs if not any(n in viol[r] for r in viol))}
    # ops
    cut = dt.datetime(2026, 9, 30, 23, 59, 59)
    breach = defaultdict(int)
    for t in tickets:
        end = t["closed"] or cut
        if (end - t["opened"]).total_seconds() / 3600 > sla[t["priority"]]:
            breach[t["team"]] += 1
    G["D1"] = {"breaches_by_team": {k: breach.get(k, 0) for k in ["Engineering", "Finance", "Ops", "Security"]}}
    p1 = [(t["closed"] - t["opened"]).total_seconds() / 3600 for t in tickets if t["priority"] == "P1" and t["closed"]]
    G["D2"] = {"p1_closed_count": len(p1), "p1_median_hours": round(statistics.median(p1), 1)}
    G["D3"] = {"active_headcount_2026_09_30": {d: sum(1 for e in emps if e["dept"] == d and e["hire_date"] <= Q_END and not (e["termination_date"] and e["termination_date"] <= Q_END)) for d in sorted(DEPTS)}}
    G["D4"] = {"expense_ids": sorted(x["expense_id"] for x in expenses if x["amount_usd"] > limits[x["category"]] and x["manager_approval"] != "yes")}
    gl_v = sorted({g["vendor_id"] for g in gl})
    G["D5"] = {"not_in_master": sorted(v for v in gl_v if v not in master_ids),
               "inactive_in_master": sorted(v for v in gl_v if master_ids.get(v) == "inactive")}

    # ---- tasks: prompt (agent-facing) + criteria (grader-only)
    T = TASKS
    PRIV.mkdir(parents=True)
    (PUB / "tasks").mkdir(parents=True)
    manifest = []
    for tid, (family, prompt, schema, criteria) in T.items():
        task_md = (f"# Task {tid}\n\nYou work on the Ops & Audit desk at Halvorsen Freight. The company's shared "
                   f"drive export is in `world/` (start with world/README.md).\n\n{prompt}\n\n"
                   f"## Deliverable\n\nWrite exactly one file, `output/answer.json`, with this shape:\n\n```json\n{schema}\n```\n\n"
                   "Use the exact key names shown. Lists of IDs may be in any order. Do not include anything else in the file.\n")
        (PUB / "tasks" / f"{tid}.md").write_text(task_md)
        manifest.append({"task_id": tid, "family": family, "criteria": criteria, "gold": G[tid]})
    (PRIV / "tasks_gold.json").write_text(json.dumps(manifest, indent=2, default=str))
    # freeze: hash every public and private file
    h = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(list(PUB.rglob("*")) + list(PRIV.rglob("*"))) if p.is_file()}
    (HERE / "FREEZE.json").write_text(json.dumps({"seed": SEED, "n_files": len(h),
        "sha256_of_all": hashlib.sha256(json.dumps(h, sort_keys=True).encode()).hexdigest(), "files": h}, indent=1))
    print(f"world: {sum(1 for p in W.rglob('*') if p.is_file())} files; tasks: {len(T)}; "
          f"criteria: {sum(len(t[3]) for t in T.values())}")
    for m in manifest:
        g = json.dumps(m["gold"], default=str)
        print(f"  {m['task_id']} [{m['family']}] {len(m['criteria'])} criteria  gold={g[:110]}")


# criteria types (see grade_world.py): schema, set_recall, set_precision, set_exact, num_close,
# dict_exact, list_exact, field_exact
TASKS = {
 "R1": ("reconciliation", "Reconcile the Q3 general ledger against the bank statement. A ledger entry is matched by a bank line with the same currency and amount whose value_date is 0 to 3 days on or after the entry's txn_date; each bank line can match at most one entry (match earliest entries first). List every ledger entry with no match.",
        '{"unmatched_entry_ids": ["GL5012", "..."]}',
        [["schema", {"unmatched_entry_ids": "list"}], ["set_recall", "unmatched_entry_ids"], ["set_precision", "unmatched_entry_ids"], ["count", "unmatched_entry_ids"]]),
 "R2": ("reconciliation", "Using the same matching rule as a ledger-to-bank reconciliation (same currency and amount, bank value_date 0-3 days on or after the ledger txn_date, one-to-one, earliest ledger entries first), find every bank line that no ledger entry explains. Report their references and their total in USD using finance/fx_rates_q3_avg.csv, rounded to cents.",
        '{"bank_only_refs": ["BK90001", "..."], "total_usd": 0.00}',
        [["schema", {"bank_only_refs": "list", "total_usd": "number"}], ["set_recall", "bank_only_refs"], ["set_precision", "bank_only_refs"], ["num_close", "total_usd", 0.01]]),
 "R3": ("reconciliation", "Find duplicate vendor payments in the general ledger: two entries for the same vendor_id with the same currency and amount whose txn_dates are 1 to 7 days apart. Report each pair once as [earlier_or_lower_id, other_id] sorted by entry_id.",
        '{"duplicate_pairs": [["GL5001", "GL5299"], "..."]}',
        [["schema", {"duplicate_pairs": "list"}], ["pairs_recall", "duplicate_pairs"], ["pairs_precision", "duplicate_pairs"], ["count", "duplicate_pairs"]]),
 "R4": ("reconciliation", "Rank vendors by total Q3 general-ledger spend converted to USD with finance/fx_rates_q3_avg.csv. Report the top 5, highest first, with USD rounded to cents.",
        '{"top5": [{"vendor_id": "V001", "usd": 0.00}, "..."]}',
        [["schema", {"top5": "list"}], ["topk_ids_ordered", "top5"], ["topk_first_value", "top5", 1.0], ["topk_ids_set", "top5"]]),
 "R5": ("reconciliation", "Quarter-end cutoff review: list every general-ledger entry whose transaction happened in Q3 2026 (txn_date on or before 2026-09-30) but was posted after the quarter closed (posted_date after 2026-09-30).",
        '{"late_posted_entry_ids": ["GL5100", "..."]}',
        [["schema", {"late_posted_entry_ids": "list"}], ["set_recall", "late_posted_entry_ids"], ["set_precision", "late_posted_entry_ids"]]),
 "L1": ("log-forensics", "From logs/auth_2026-09.log, identify brute-force sources: any source IP with 10 or more Failed attempts inside any 5-minute (300-second) window.",
        '{"ips": ["1.2.3.4", "..."]}',
        [["schema", {"ips": "list"}], ["set_recall", "ips"], ["set_precision", "ips"], ["count", "ips"]]),
 "L2": ("log-forensics", "For every brute-force source IP (10+ Failed attempts inside any 300-second window in logs/auth_2026-09.log), check whether that IP later had an Accepted login. Report each such IP with the username and timestamp of its first Accepted login after its first failure.",
        '{"compromised": [{"ip": "1.2.3.4", "user": "name", "time": "2026-09-01T00:00:00"}, "..."]}',
        [["schema", {"compromised": "list"}], ["records_key_set", "compromised", "ip"], ["records_exact", "compromised", ["ip", "user", "time"]]]),
 "L3": ("log-forensics", "Cross-reference logs/auth_2026-09.log with hr/employees.csv. List every username that had an Accepted login on a date after that employee's termination_date.",
        '{"usernames": ["first.last", "..."]}',
        [["schema", {"usernames": "list"}], ["set_recall", "usernames"], ["set_precision", "usernames"]]),
 "L4": ("log-forensics", "Detect impossible travel in logs/vpn_sessions_2026-09.csv: a user whose consecutive VPN sessions come from two different geo_country values less than 2 hours apart.",
        '{"usernames": ["first.last", "..."]}',
        [["schema", {"usernames": "list"}], ["set_recall", "usernames"], ["set_precision", "usernames"], ["count", "usernames"]]),
 "L5": ("log-forensics", "Summarise logs/auth_2026-09.log: how many Accepted logins hit host db-admin, and how many Failed attempts are in the whole log?",
        '{"accepted_db_admin_logins": 0, "failed_total": 0}',
        [["schema", {"accepted_db_admin_logins": "number", "failed_total": "number"}], ["num_close", "accepted_db_admin_logins", 0], ["num_close", "failed_total", 0]]),
 "C1": ("contract-compliance", "Check every invoice in invoices/ against its vendor's MSA in vendors/contracts/. List every invoice with at least one line whose unit_price exceeds the contract's rate cap for that service.",
        '{"invoice_ids": ["INV-3001", "..."]}',
        [["schema", {"invoice_ids": "list"}], ["set_recall", "invoice_ids"], ["set_precision", "invoice_ids"], ["count", "invoice_ids"]]),
 "C2": ("contract-compliance", "List every invoice dated after its vendor's contract expiry date (see vendors/contracts/).",
        '{"invoice_ids": ["INV-3001", "..."]}',
        [["schema", {"invoice_ids": "list"}], ["set_recall", "invoice_ids"], ["set_precision", "invoice_ids"]]),
 "C3": ("contract-compliance", "Audit invoice arithmetic. For every invoice whose stated total differs from the sum of qty * unit_price over its lines (by more than half a cent), report the invoice and the correct total rounded to cents.",
        '{"errors": [{"invoice_id": "INV-3001", "correct_total": 0.00}, "..."]}',
        [["schema", {"errors": "list"}], ["records_key_set", "errors", "invoice_id"], ["records_num", "errors", "invoice_id", "correct_total", 0.01]]),
 "C4": ("contract-compliance", "List every invoice that bills at least one service that is not in that vendor's approved services list in its MSA.",
        '{"invoice_ids": ["INV-3001", "..."]}',
        [["schema", {"invoice_ids": "list"}], ["set_recall", "invoice_ids"], ["set_precision", "invoice_ids"], ["count", "invoice_ids"]]),
 "C5": ("contract-compliance", "Overbilling exposure: for each vendor, sum (unit_price - cap) * qty over every invoice line priced above its contract cap. Report the vendor with the largest total, the amount rounded to cents, and its billing currency.",
        '{"vendor_id": "V001", "overbilled_amount": 0.00, "currency": "USD"}',
        [["schema", {"vendor_id": "string", "overbilled_amount": "number", "currency": "string"}], ["field_exact", "vendor_id"], ["num_close", "overbilled_amount", 0.05], ["field_exact", "currency"]]),
 "K1": ("config-audit", "Audit configs/services/*.yaml against policy/security_baseline.md rule SB-1. List the services that violate it.",
        '{"services": ["service-name", "..."]}',
        [["schema", {"services": "list"}], ["set_recall", "services"], ["set_precision", "services"]]),
 "K2": ("config-audit", "Apply rule SB-4 of policy/security_baseline.md to every service config. For each violating service, list the admin entries that are not active employees.",
        '{"bad_admins": {"service-name": ["user"], "...": []}}',
        [["schema", {"bad_admins": "dict"}], ["dict_keys_exact", "bad_admins"], ["dict_exact", "bad_admins"]]),
 "K3": ("config-audit", "List the services that violate rule SB-3 (log retention) of policy/security_baseline.md.",
        '{"services": ["service-name", "..."]}',
        [["schema", {"services": "list"}], ["set_recall", "services"], ["set_precision", "services"]]),
 "K4": ("config-audit", "List the services that violate rule SB-2 (remote shell exposure) of policy/security_baseline.md.",
        '{"services": ["service-name", "..."]}',
        [["schema", {"services": "list"}], ["set_recall", "services"], ["set_precision", "services"], ["count", "services"]]),
 "K5": ("config-audit", "Run the full security baseline (SB-1 to SB-5) over every service config. Report how many services violate each rule, and which services violate none.",
        '{"violations_by_rule": {"SB-1": 0, "SB-2": 0, "SB-3": 0, "SB-4": 0, "SB-5": 0}, "clean_services": ["..."]}',
        [["schema", {"violations_by_rule": "dict", "clean_services": "list"}], ["dict_exact", "violations_by_rule"], ["set_recall", "clean_services"], ["set_precision", "clean_services"]]),
 "D1": ("ops-reporting", "Using policy/sla_policy.md and ops/tickets_2026Q3.csv, count SLA breaches per team.",
        '{"breaches_by_team": {"Engineering": 0, "Finance": 0, "Ops": 0, "Security": 0}}',
        [["schema", {"breaches_by_team": "dict"}], ["dict_exact", "breaches_by_team"], ["dict_close_frac", "breaches_by_team", 0.5]]),
 "D2": ("ops-reporting", "For closed P1 tickets in ops/tickets_2026Q3.csv, report how many there are and the median resolution time in hours (closed - opened), rounded to one decimal.",
        '{"p1_closed_count": 0, "p1_median_hours": 0.0}',
        [["schema", {"p1_closed_count": "number", "p1_median_hours": "number"}], ["num_close", "p1_closed_count", 0], ["num_close", "p1_median_hours", 0.051]]),
 "D3": ("ops-reporting", "From hr/employees.csv, report active headcount per department as of 2026-09-30: hired on or before that date and not terminated on or before that date.",
        '{"active_headcount_2026_09_30": {"Engineering": 0, "...": 0}}',
        [["schema", {"active_headcount_2026_09_30": "dict"}], ["dict_keys_exact", "active_headcount_2026_09_30"], ["dict_exact", "active_headcount_2026_09_30"]]),
 "D4": ("ops-reporting", "Apply policy/expense_policy.md to finance/expenses_2026Q3.csv. List every expense that breaks the policy.",
        '{"expense_ids": ["X4000", "..."]}',
        [["schema", {"expense_ids": "list"}], ["set_recall", "expense_ids"], ["set_precision", "expense_ids"], ["count", "expense_ids"]]),
 "D5": ("ops-reporting", "Vendor master hygiene: among the vendor_ids that appear in the general ledger, which are missing from vendors/vendor_master.csv, and which are present but marked inactive?",
        '{"not_in_master": ["V000"], "inactive_in_master": ["V000"]}',
        [["schema", {"not_in_master": "list", "inactive_in_master": "list"}], ["set_exact", "not_in_master"], ["set_exact", "inactive_in_master"]]),
}

if __name__ == "__main__":
    main()
