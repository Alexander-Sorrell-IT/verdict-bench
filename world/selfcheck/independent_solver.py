"""Independent solver: reads ONLY world/public, solves all 25 tasks from the task wording.
Its agreement with the generator's gold is the check that task prompts and gold line up."""
import json, sys, re, glob, statistics, datetime as dt
from collections import defaultdict
from pathlib import Path
import pandas as pd, yaml
W = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
gl = pd.read_csv(W/"finance/general_ledger_2026Q3.csv", parse_dates=["txn_date","posted_date"])
bk = pd.read_csv(W/"finance/bank_statement_2026Q3.csv", parse_dates=["value_date"])
fx = dict(pd.read_csv(W/"finance/fx_rates_q3_avg.csv").values)
emp = pd.read_csv(W/"hr/employees.csv", parse_dates=["hire_date","termination_date"])
ans = {}
# R1/R2
used=set(); um=[]
for _,g in gl.sort_values(["txn_date","entry_id"]).iterrows():
    c = bk[(bk.currency==g.currency)&(abs(bk.amount-g.amount)<0.001)&(~bk.bank_ref.isin(used))]
    c = c[((c.value_date-g.txn_date).dt.days>=0)&((c.value_date-g.txn_date).dt.days<=3)]
    if len(c): used.add(c.sort_values(["value_date","bank_ref"]).iloc[0].bank_ref)
    else: um.append(g.entry_id)
ans["R1"]={"unmatched_entry_ids":um}
bo=bk[~bk.bank_ref.isin(used)]
ans["R2"]={"bank_only_refs":list(bo.bank_ref),"total_usd":round(sum(r.amount*fx[r.currency] for r in bo.itertuples()),2)}
pairs=[]
for (v,c,a),grp in gl.groupby(["vendor_id","currency","amount"]):
    rows=list(grp.itertuples())
    for i in range(len(rows)):
        for j in range(i+1,len(rows)):
            d=abs((rows[j].txn_date-rows[i].txn_date).days)
            if 1<=d<=7: pairs.append(sorted([rows[i].entry_id,rows[j].entry_id]))
ans["R3"]={"duplicate_pairs":pairs}
gl["usd"]=gl.amount*gl.currency.map(fx)
s=gl.groupby("vendor_id").usd.sum().sort_values(ascending=False).head(5)
ans["R4"]={"top5":[{"vendor_id":k,"usd":round(v,2)} for k,v in s.items()]}
q=pd.Timestamp("2026-09-30")
ans["R5"]={"late_posted_entry_ids":list(gl[(gl.txn_date<=q)&(gl.posted_date>q)].entry_id)}
# logs
rx=re.compile(r"^(\S+) (\S+) sshd: (Accepted|Failed) password for (\S+) from (\S+) port")
L=[]
for line in open(W/"logs/auth_2026-09.log"):
    m=rx.match(line); t,h,res,u,ip=m.groups(); L.append((pd.Timestamp(t),h,res,u,ip))
la=pd.DataFrame(L,columns=["t","host","res","user","ip"])
br=[]
for ip,g in la[la.res=="Failed"].groupby("ip"):
    ts=sorted(g.t)
    if any(sum(1 for x in ts[i:] if (x-ts[i]).total_seconds()<=300)>=10 for i in range(len(ts))): br.append(ip)
ans["L1"]={"ips":br}
comp=[]
for ip in br:
    ff=la[(la.ip==ip)&(la.res=="Failed")].t.min()
    ok=la[(la.ip==ip)&(la.res=="Accepted")&(la.t>ff)].sort_values("t")
    if len(ok): r=ok.iloc[0]; comp.append({"ip":ip,"user":r.user,"time":r.t.strftime("%Y-%m-%dT%H:%M:%S")})
ans["L2"]={"compromised":comp}
term=dict(emp.dropna(subset=["termination_date"])[["username","termination_date"]].values)
ans["L3"]={"usernames":sorted({r.user for r in la[la.res=="Accepted"].itertuples() if r.user in term and r.t.normalize()>term[r.user]})}
vp=pd.read_csv(W/"logs/vpn_sessions_2026-09.csv",parse_dates=["session_start"]).sort_values(["username","session_start"])
imp=set()
for u,g in vp.groupby("username"):
    r=list(g.itertuples())
    for a,b in zip(r,r[1:]):
        if a.geo_country!=b.geo_country and (b.session_start-a.session_start).total_seconds()<7200: imp.add(u)
ans["L4"]={"usernames":sorted(imp)}
ans["L5"]={"accepted_db_admin_logins":int(((la.res=="Accepted")&(la.host=="db-admin")).sum()),"failed_total":int((la.res=="Failed").sum())}
# contracts
con={}
for p in glob.glob(str(W/"vendors/contracts/*.md")):
    tx=open(p).read(); vid=Path(p).stem
    caps={m.group(1):float(m.group(2)) for m in re.finditer(r"^- ([a-z-]+): ([\d.]+)$",tx,re.M)}
    exp=pd.Timestamp(re.search(r"Expiry date: (\S+)",tx).group(1)); cur=re.search(r"Billing currency: (\S+)",tx).group(1)
    con[vid]=(caps,exp,cur)
inv=[json.load(open(p)) for p in sorted(glob.glob(str(W/"invoices/*.json")))]
c1,c2,c3,c4=set(),set(),[],set(); ob=defaultdict(float)
for i in inv:
    caps,exp,cur=con[i["vendor_id"]]
    for l in i["lines"]:
        if l["service"] in caps and l["unit_price"]>caps[l["service"]]: c1.add(i["invoice_id"]); ob[i["vendor_id"]]+=(l["unit_price"]-caps[l["service"]])*l["qty"]
        if l["service"] not in caps: c4.add(i["invoice_id"])
    if pd.Timestamp(i["date"])>exp: c2.add(i["invoice_id"])
    tr=round(sum(l["qty"]*l["unit_price"] for l in i["lines"]),2)
    if abs(tr-i["total"])>0.005: c3.append({"invoice_id":i["invoice_id"],"correct_total":tr})
ans["C1"]={"invoice_ids":sorted(c1)}; ans["C2"]={"invoice_ids":sorted(c2)}; ans["C3"]={"errors":c3}; ans["C4"]={"invoice_ids":sorted(c4)}
w=max(ob,key=ob.get); ans["C5"]={"vendor_id":w,"overbilled_amount":round(ob[w],2),"currency":con[w][2]}
# configs
act=set(emp[emp.status=="active"].username)
svc={Path(p).stem:yaml.safe_load(open(p)) for p in glob.glob(str(W/"configs/services/*.yaml"))}
V=defaultdict(list)
for n,s in svc.items():
    if float(s["tls"]["min_version"])<1.2: V["SB-1"].append(n)
    if any(p["port"] in (22,23) and p["exposure"]=="public" for p in s["ports"]): V["SB-2"].append(n)
    if s["logging"]["retention_days"]<90: V["SB-3"].append(n)
    if any(a not in act for a in s["admins"]): V["SB-4"].append(n)
    if s["root_login"]: V["SB-5"].append(n)
ans["K1"]={"services":V["SB-1"]}; ans["K3"]={"services":V["SB-3"]}; ans["K4"]={"services":V["SB-2"]}
ans["K2"]={"bad_admins":{n:[a for a in s["admins"] if a not in act] for n,s in svc.items() if any(a not in act for a in s["admins"])}}
ans["K5"]={"violations_by_rule":{r:len(V[r]) for r in ["SB-1","SB-2","SB-3","SB-4","SB-5"]},"clean_services":[n for n in svc if not any(n in v for v in V.values())]}
# ops
tk=pd.read_csv(W/"ops/tickets_2026Q3.csv",parse_dates=["opened","closed"]); sla={"P1":4,"P2":24,"P3":72}
end=tk.closed.fillna(pd.Timestamp("2026-09-30T23:59:59")); hrs=(end-tk.opened).dt.total_seconds()/3600
br_=tk[hrs>tk.priority.map(sla)].groupby("team").size()
ans["D1"]={"breaches_by_team":{t:int(br_.get(t,0)) for t in ["Engineering","Finance","Ops","Security"]}}
p1=tk[(tk.priority=="P1")&tk.closed.notna()]; h=((p1.closed-p1.opened).dt.total_seconds()/3600)
ans["D2"]={"p1_closed_count":len(p1),"p1_median_hours":round(float(h.median()),1)}
a=emp[(emp.hire_date<=q)&~(emp.termination_date<=q)]
ans["D3"]={"active_headcount_2026_09_30":{d:int((a.dept==d).sum()) for d in sorted(emp.dept.unique())}}
ex=pd.read_csv(W/"finance/expenses_2026Q3.csv"); lim={"meals":75,"lodging":250,"airfare":900,"ground":120,"supplies":200}
ans["D4"]={"expense_ids":list(ex[(ex.amount_usd>ex.category.map(lim))&(ex.manager_approval!="yes")].expense_id)}
vm=pd.read_csv(W/"vendors/vendor_master.csv"); glv=set(gl.vendor_id)
ans["D5"]={"not_in_master":sorted(glv-set(vm.vendor_id)),"inactive_in_master":sorted(glv&set(vm[vm.status=="inactive"].vendor_id))}
for k,v in ans.items(): (OUT/f"{k}.json").write_text(json.dumps(v,default=str))
print("solved",len(ans))
