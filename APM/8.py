"""
build_apm_report.py  -  MaximoLab APM Report (Power BI-style, standalone)
-------------------------------------------------------------------------
Layout follows common Power BI report practice:
  * left navigation pages, slicers on top, KPI cards with target + RAG status top-left
  * 3-30-300 rule: overview in 3 s, filter in 30 s, details on demand in 300 s
  * every KPI shows its formula with live numbers ("How it is calculated")
  * click any asset row -> drill-through panel with the full step-by-step calculation

Pages: Overview | Data Quality (ADQS) | Asset Health (AHI) | Criticality (ACI)
       Health x Criticality | Investment Plan (AIP) | Data Completeness (DCI) | Methodology

Usage (PowerShell):
    cd C:\\MaximoLab
    python build_apm_report.py          # live MaximoLab data (run apm_all_in_one.sql in SSMS first)
    python build_apm_report.py --demo   # sample data, no database needed
If "No module named pyodbc":  python -m pip install pyodbc
"""
import datetime
import decimal
import json
import os
import random
import sys
import webbrowser

SERVER = r"ASDL211820\SQLEXPRESS"
DATABASE = "MaximoLab"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "apm_report.html")

RULE_COLS = ["C_Desc", "C_Meter", "C_Reading", "C_PM", "C_WOHist",
             "V_Priority", "V_Status", "V_Unit", "V_Range", "V_Dates",
             "A_LastRead", "A_Outlier", "A_LabHrs",
             "S_LocSite", "S_WOSite", "S_WOLoc", "S_StatAct",
             "T_Reading", "T_PM", "T_WO"]

ASSET_SQL = f"""
SELECT p.ASSETNUM, p.SITEID, p.LOCATION, p.DESCRIPTION,
       p.AHI, p.HealthBand, p.ACI, p.CriticalityBand, p.ADQS, p.MaintCost,
       p.RiskScore, p.RiskRank, p.Action, p.Horizon, p.Confidence,
       h.FailureScore, h.PMScore, h.ConditionScore, h.BacklogScore,
       h.CorrectiveWO, h.PMCount, h.PMOverdue, h.OpenWO,
       c.PRIORITY, c.PriorityScore, c.CostScore, c.FrequencyScore,
       {", ".join("d." + r for r in RULE_COLS)},
       d.Completeness, d.Validity, d.Accuracy, d.Consistency, d.Timeliness, d.ADQSBand,
       z.Quadrant, z.Strategy, z.DataFlag
FROM apm.vw_AIP p
JOIN apm.vw_AHI         h ON h.ASSETNUM = p.ASSETNUM
JOIN apm.vw_ACI         c ON c.ASSETNUM = p.ASSETNUM
JOIN apm.vw_DQ_Asset    d ON d.ASSETNUM = p.ASSETNUM
JOIN apm.vw_HC_Quadrant z ON z.ASSETNUM = p.ASSETNUM
ORDER BY p.RiskRank
"""
DCI_SQL = "SELECT Area, CheckName, TotalRows, FailRows, CompletenessPct FROM apm.vw_DCI ORDER BY Area, CheckName"


# ---------------------------------------------------------------- database
def connect():
    try:
        import pyodbc
    except ImportError:
        sys.exit("pyodbc is not installed. Run:  python -m pip install pyodbc   (or use --demo)")
    installed = pyodbc.drivers()
    driver = next((d for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
                               "SQL Server") if d in installed), None)
    if driver is None:
        sys.exit(f"No SQL Server ODBC driver found. Installed drivers: {installed}")
    last_error = None
    for server in (SERVER, "lpc:" + SERVER):          # lpc: = shared memory, works without TCP/IP
        try:
            conn = pyodbc.connect(f"DRIVER={{{driver}}};SERVER={server};DATABASE={DATABASE};"
                                  "Trusted_Connection=yes;TrustServerCertificate=yes;", timeout=10)
            print(f"Connected: {server} using {driver}")
            return conn
        except pyodbc.Error as e:
            last_error = e
    sys.exit(f"Could not connect to {SERVER}: {last_error}")


def clean(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    return v


def fetch(cur, sql):
    cur.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, (clean(v) for v in row))) for row in cur.fetchall()]


def load_live():
    conn = connect()
    cur = conn.cursor()
    try:
        assets, dci = fetch(cur, ASSET_SQL), fetch(cur, DCI_SQL)
    except Exception as e:
        sys.exit("Query failed - run apm_all_in_one.sql in SSMS first.\n" + str(e))
    conn.close()
    return assets, dci


# ---------------------------------------------------------------- demo data (same formulas as SQL)
def load_demo():
    random.seed(11)
    sites = ["BEDFORD", "NASHUA", "TEXAS"]
    kinds = ["Power Transformer", "Circuit Breaker", "Feeder Line", "Pump", "Substation Battery",
             "Switchgear", "Recloser", "Capacitor Bank"]
    rows = []
    for i in range(1, 81):
        has_meter = random.random() > 0.2
        has_read = has_meter and random.random() > 0.1
        pm_count = random.choice([0, 1, 1, 2, 3])
        wo_count = random.choice([0, 1, 2, 3, 5, 8])
        has_comp = wo_count > 0 and random.random() > 0.3
        p = lambda x: 1 if random.random() < x else 0
        r = dict(ASSETNUM=f"A{1000 + i}", SITEID=random.choice(sites),
                 DESCRIPTION=random.choice(kinds) if random.random() > 0.1 else None,
                 PRIORITY=random.randint(1, 5), CorrectiveWO=min(wo_count, random.choice([0, 0, 1, 1, 2, 3, 4, 6])),
                 PMCount=pm_count, PMOverdue=random.randint(0, pm_count),
                 OpenWO=min(wo_count, random.choice([0, 0, 1, 2, 3, 5])),
                 Dev=random.choice([5, 12, 25, 40, 70, 110]) if has_read else None,
                 MaintCost=round(random.uniform(500, 40000), 2) if wo_count else 0.0)
        r["LOCATION"] = f"{r['SITEID'][:3]}-LOC{random.randint(1, 6):02d}"
        r.update(C_Desc=1 if r["DESCRIPTION"] else 0, C_Meter=int(has_meter),
                 C_Reading=(int(has_read) if has_meter else None), C_PM=int(pm_count > 0), C_WOHist=int(wo_count > 0),
                 V_Priority=1, V_Status=p(0.95), V_Unit=(p(0.85) if has_meter else None),
                 V_Range=(p(0.95) if has_read else None), V_Dates=(p(0.95) if (has_read or wo_count) else None),
                 A_LastRead=(p(0.75) if has_read else None), A_Outlier=(p(0.85) if has_read else None),
                 A_LabHrs=(p(0.7) if has_comp else None),
                 S_LocSite=p(0.88), S_WOSite=(p(0.95) if wo_count else None), S_WOLoc=(p(0.8) if wo_count else None),
                 S_StatAct=p(0.95),
                 T_Reading=(p(0.65) if has_meter else None), T_PM=(int(r["PMOverdue"] == 0 or random.random() > .5) if pm_count else None),
                 T_WO=(p(0.8) if wo_count else None))
        rows.append(r)

    def dim(r, keys):
        vals = [r[k] for k in keys if r[k] is not None]
        return round(100 * sum(vals) / len(vals), 1) if vals else None

    max_cost = max(r["MaintCost"] for r in rows) or 1
    max_cm = max(r["CorrectiveWO"] for r in rows) or 1
    out = []
    for r in rows:
        dims = dict(Completeness=dim(r, RULE_COLS[0:5]), Validity=dim(r, RULE_COLS[5:10]),
                    Accuracy=dim(r, RULE_COLS[10:13]), Consistency=dim(r, RULE_COLS[13:17]),
                    Timeliness=dim(r, RULE_COLS[17:20]))
        dv = [v for v in dims.values() if v is not None]
        adqs = round(sum(dv) / len(dv), 1)
        fs = 100 - min(r["CorrectiveWO"] * 15, 100)
        ps = 50.0 if r["PMCount"] == 0 else round(100 * (r["PMCount"] - r["PMOverdue"]) / r["PMCount"], 1)
        cs = 50.0 if r["Dev"] is None else max(0, 100 - r["Dev"])
        bs = 100 - min(r["OpenWO"] * 10, 100)
        ahi = round(0.35 * fs + 0.25 * ps + 0.25 * cs + 0.15 * bs, 1)
        pr = (6 - r["PRIORITY"]) * 20
        co = round(100 * r["MaintCost"] / max_cost, 1)
        fq = round(100 * r["CorrectiveWO"] / max_cm, 1)
        aci = round(0.5 * pr + 0.3 * co + 0.2 * fq, 1)
        risk = round((100 - ahi) * aci / 100, 1)
        qd = "Q1" if ahi < 60 and aci >= 60 else "Q2" if ahi < 60 else "Q3" if aci >= 60 else "Q4"
        if ahi < 40 and aci >= 60:
            act, hz = "Replace", "Year 1"
        elif ahi < 60:
            act, hz = "Refurbish / Overhaul", "Year 1-2"
        elif ahi < 80 and aci >= 60:
            act, hz = "Increase inspection", "Year 2-3"
        else:
            act, hz = "Run & monitor", "Year 3-5"
        a = {k: r[k] for k in ["ASSETNUM", "SITEID", "LOCATION", "DESCRIPTION", "PRIORITY", "CorrectiveWO",
                               "PMCount", "PMOverdue", "OpenWO", "MaintCost"] + RULE_COLS}
        a.update(dims, ADQS=adqs, ADQSBand="Trusted" if adqs >= 80 else "Usable" if adqs >= 60 else "Fix data first",
                 FailureScore=fs, PMScore=ps, ConditionScore=cs, BacklogScore=bs, AHI=ahi,
                 HealthBand="Good" if ahi >= 80 else "Fair" if ahi >= 60 else "Poor" if ahi >= 40 else "Critical",
                 PriorityScore=pr, CostScore=co, FrequencyScore=fq, ACI=aci,
                 CriticalityBand="High" if aci >= 60 else "Medium" if aci >= 40 else "Low",
                 RiskScore=risk, Action=act, Horizon=hz, Quadrant=qd,
                 Strategy={"Q1": "Act now", "Q2": "Fix cheaply / run to failure", "Q3": "Protect", "Q4": "Optimise"}[qd],
                 Confidence="High" if adqs >= 80 else "Medium" if adqs >= 60 else "Low",
                 DataFlag="Verify data before acting" if adqs < 60 else "OK")
        out.append(a)
    out.sort(key=lambda x: -x["RiskScore"])
    for i, a in enumerate(out, 1):
        a["RiskRank"] = i
    checks = [("Asset", "Description filled", 80), ("Asset", "Location belongs to asset site", 80),
              ("Asset", "Has at least one meter", 80), ("Asset", "Covered by a PM", 80),
              ("Meter", "Meter has readings", 110), ("Meter", "Reading in last 180 days", 110),
              ("Work Order", "Asset linked", 260), ("Work Order", "Location filled", 260),
              ("Work Order", "Description filled", 260), ("Work Order", "WO location matches asset location", 240),
              ("Work Order", "PM work order linked to PM", 90), ("Work Order", "PM work order has job plan", 90),
              ("Work Order", "Completed WO has labor actuals", 150), ("Work Order", "Completed WO has ACTLABHRS > 0", 150),
              ("Work Order", "Has status history", 260), ("Reference", "Location description filled", 20),
              ("Reference", "Item description filled", 40), ("Reference", "Job plan description filled", 15),
              ("Reference", "Person display name filled", 25)]
    dci = []
    for area, name, total in checks:
        fail = int(total * random.choice([0, 0.02, 0.05, 0.1, 0.2, 0.35]))
        dci.append(dict(Area=area, CheckName=name, TotalRows=total, FailRows=fail,
                        CompletenessPct=round(100 * (total - fail) / total, 1)))
    return out, dci


# ---------------------------------------------------------------- html
HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MaximoLab APM Report</title>
<style>
:root{--nav:#1b2a4a;--bg:#eef1f6;--card:#fff;--ink:#1f2937;--mute:#6b7280;--line:#e5e7eb;--acc:#2563eb;
--g:#16a34a;--a:#d97706;--o:#ea580c;--r:#dc2626}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--ink);font-size:14px}
.shell{display:grid;grid-template-columns:210px minmax(0,1fr);min-height:100vh}
nav{background:var(--nav);color:#cbd5e1;padding:16px 0}
nav .brand{color:#fff;font-weight:700;font-size:15px;padding:0 18px 4px}nav .sub{font-size:11px;padding:0 18px 16px;color:#94a3b8}
nav button{display:block;width:100%;text-align:left;background:none;border:0;color:#cbd5e1;padding:10px 18px;font-size:13.5px;cursor:pointer;border-left:3px solid transparent}
nav button:hover{background:#24365c}nav button.on{background:#24365c;color:#fff;border-left-color:#60a5fa;font-weight:600}
nav button:focus-visible{outline:2px solid #60a5fa}
main{padding:16px 20px;min-width:0}
.head{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;justify-content:space-between;margin-bottom:14px}
.head h1{margin:0;font-size:20px}.head .q{color:var(--mute);font-size:13px;margin-top:2px}
.slicers{display:flex;flex-wrap:wrap;gap:8px}
.slicers label{font-size:11px;color:var(--mute);display:flex;flex-direction:column;gap:2px}
.slicers select,.slicers input{padding:5px 8px;border:1px solid #cbd5e1;border-radius:4px;font-size:13px;background:#fff;min-width:120px}
.btn{padding:6px 10px;border:1px solid #cbd5e1;background:#fff;border-radius:4px;cursor:pointer;font-size:12px}
.page{display:none}.page.on{display:block}
.g{display:grid;gap:14px}.kp{grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.c2{grid-template-columns:repeat(auto-fit,minmax(360px,1fr))}.c3{grid-template-columns:2fr 1fr}
@media(max-width:1000px){.c3{grid-template-columns:1fr}.shell{grid-template-columns:1fr}nav{display:flex;overflow-x:auto;padding:0}nav .brand,nav .sub{display:none}nav button{white-space:nowrap;border-left:0;border-bottom:3px solid transparent}}
.card{background:var(--card);border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.06);padding:14px 16px;min-width:0}
.card h3{margin:0 0 2px;font-size:14px}.card .hint{font-size:12px;color:var(--mute);margin-bottom:10px}
.kpi{border-top:4px solid var(--g);cursor:default}.kpi .l{font-size:12px;color:var(--mute);font-weight:600}
.kpi .v{font-size:30px;font-weight:700;margin:4px 0 0}.kpi .t{font-size:12px;color:var(--mute)}
.kpi .s{font-size:12px;font-weight:600;margin-top:4px}
.formula{display:flex;flex-wrap:wrap;align-items:stretch;gap:8px;margin-bottom:14px}
.fx{background:#fff;border-radius:6px;padding:10px 12px;box-shadow:0 1px 2px rgba(0,0,0,.06);min-width:150px;flex:1}
.fx .w{font-size:11px;color:var(--mute);font-weight:600;text-transform:uppercase}.fx .n{font-size:13px;margin:2px 0}
.fx .c{font-size:18px;font-weight:700}.op{display:flex;align-items:center;font-size:22px;color:var(--mute);font-weight:700}
.fx.res{background:#1b2a4a;color:#fff}.fx.res .w{color:#93c5fd}
.bar{display:grid;grid-template-columns:minmax(110px,40%) 1fr 64px;gap:8px;align-items:center;margin:6px 0;font-size:13px}
.bar .tr{background:#eef1f5;height:14px;border-radius:3px;overflow:hidden}.bar .fl{height:100%}
.bar .v{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;background:#f8fafc;color:#475569;padding:7px 8px;border-bottom:1px solid var(--line);position:sticky;top:0;cursor:pointer;white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tbody tr.click{cursor:pointer}tbody tr.click:hover{background:#eff6ff}
.tw{max-height:420px;overflow:auto}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;font-weight:600;color:#fff;white-space:nowrap}
.note{background:#eff6ff;border-left:3px solid var(--acc);padding:8px 12px;border-radius:4px;font-size:12.5px;line-height:1.5;margin-bottom:14px}
.quad{display:grid;grid-template-columns:24px 1fr 1fr;gap:10px}
.qd{border-radius:6px;padding:14px;cursor:pointer;border:2px solid transparent}.qd.sel{border-color:#1f2937}
.qd h4{margin:0;font-size:14px}.qd .big{font-size:28px;font-weight:700}.qd .ap{font-size:12px;margin-top:4px;line-height:1.4}
.ax{font-size:11px;color:var(--mute);display:flex;align-items:center;justify-content:center}.ay{writing-mode:vertical-rl;transform:rotate(180deg)}
.dr{position:fixed;inset:0;background:rgba(15,23,42,.45);display:none;justify-content:flex-end;z-index:9}
.dr.on{display:flex}.drp{background:#fff;width:min(560px,100%);height:100%;overflow:auto;padding:18px 20px}
.drp h2{margin:0;font-size:18px}.drp h4{margin:16px 0 6px;font-size:13px;text-transform:uppercase;color:#475569;letter-spacing:.4px}
.calc td{padding:4px 6px}.calc .r{text-align:right;font-variant-numeric:tabular-nums}
.calc tr.tot td{border-top:2px solid #1f2937;font-weight:700}
.ok{color:var(--g);font-weight:700}.ko{color:var(--r);font-weight:700}.na{color:#9ca3af}
svg text{font-family:"Segoe UI",Arial,sans-serif}
</style></head><body>
<div class="shell">
<nav id="nav"><div class="brand">MaximoLab APM</div><div class="sub">Source: __SOURCE__<br>Built __BUILT__</div></nav>
<main>
 <div class="head"><div><h1 id="ptitle"></h1><div class="q" id="pq"></div></div>
  <div class="slicers">
   <label>Site<select id="fSite"></select></label>
   <label>Health band<select id="fBand"></select></label>
   <label>Quadrant<select id="fQuad"></select></label>
   <label>Search asset<input id="fText" type="search" placeholder="e.g. A1021 / Pump"></label>
   <label>&nbsp;<button class="btn" type="button" id="fReset">Clear filters</button></label>
  </div></div>

 <section class="page" id="p-ov">
  <div class="g kp" id="ov-k"></div>
  <div class="g c3" style="margin-top:14px">
   <div class="card"><h3>Risk matrix: health vs criticality</h3><div class="hint">Each dot = one asset. Dashed lines = thresholds (AHI 60, ACI 60). Click a dot for its calculation.</div><div id="ov-m"></div></div>
   <div class="card"><h3>Assets per quadrant</h3><div class="hint">Strategy for each group</div><div id="ov-q"></div>
    <h3 style="margin-top:16px">Health bands</h3><div id="ov-h"></div></div>
  </div>
  <div class="card" style="margin-top:14px"><h3>Top 10 risk assets</h3><div class="hint">Risk = (100 − AHI) × ACI ÷ 100. Click a row to see how it was calculated.</div><div class="tw" id="ov-t"></div></div>
 </section>

 <section class="page" id="p-dq">
  <div class="note"><b>ADQS (Asset Data Quality Score)</b> = average of the 5 dimensions below. Each dimension = <b>rules passed ÷ rules that apply × 100</b>. A rule that doesn't apply (e.g. "meter unit valid" on an asset with no meter) is skipped, not failed. Target ≥ 80.</div>
  <div class="formula" id="dq-f"></div>
  <div class="g c2"><div class="card"><h3>Rule scorecard (issues log)</h3><div class="hint">20 rules · where the failures are · sorted worst first</div><div class="tw" id="dq-r"></div></div>
   <div class="card"><h3>ADQS bands</h3><div class="hint">≥80 Trusted · 60–79 Usable · &lt;60 Fix data first</div><div id="dq-b"></div>
    <h3 style="margin-top:16px">Decision confidence</h3><div class="hint">AIP confidence comes from ADQS</div><div id="dq-c"></div></div></div>
  <div class="card" style="margin-top:14px"><h3>Assets: weakest data first</h3><div class="hint">Click a row to see each rule ✔ / ✖</div><div class="tw" id="dq-t"></div></div>
 </section>

 <section class="page" id="p-ahi">
  <div class="note"><b>AHI (Asset Health Index)</b>, 0–100, where 100 = perfect. A weighted sum of 4 health drivers. The boxes show the <b>average driver score for the filtered assets</b> × its weight. Bands: ≥80 Good · 60–79 Fair · 40–59 Poor · &lt;40 Critical.</div>
  <div class="formula" id="ahi-f"></div>
  <div class="g c2"><div class="card"><h3>What pulls health down</h3><div class="hint">Points lost per driver (weight × (100 − score)), averaged across assets</div><div id="ahi-l"></div></div>
   <div class="card"><h3>Health bands</h3><div id="ahi-b"></div></div></div>
  <div class="card" style="margin-top:14px"><h3>Asset health detail</h3><div class="tw" id="ahi-t"></div></div>
 </section>

 <section class="page" id="p-aci">
  <div class="note"><b>ACI (Asset Criticality Index)</b>, 0–100: how much it hurts if the asset fails. Cost and frequency are scored relative to the highest asset in the database. Bands: ≥60 High · 40–59 Medium · &lt;40 Low (same 60 line as the quadrants).</div>
  <div class="formula" id="aci-f"></div>
  <div class="g c2"><div class="card"><h3>Criticality bands</h3><div id="aci-b"></div></div>
   <div class="card"><h3>Maintenance spend by criticality</h3><div class="hint">Labour (LABTRANS) + material (MATUSETRANS)</div><div id="aci-c"></div></div></div>
  <div class="card" style="margin-top:14px"><h3>Asset criticality detail</h3><div class="tw" id="aci-t"></div></div>
 </section>

 <section class="page" id="p-hc">
  <div class="note"><b>Health × Criticality.</b> Poor health = AHI &lt; 60. High criticality = ACI ≥ 60. Each quadrant gets one strategy. Click a quadrant to filter the table.</div>
  <div class="quad" id="hc-g"></div>
  <div class="card" style="margin-top:14px"><h3>Assets in quadrant</h3><div class="tw" id="hc-t"></div></div>
 </section>

 <section class="page" id="p-aip">
  <div class="note"><b>AIP (Asset Investment Planning).</b> Risk = (100 − AHI) × ACI ÷ 100. The action comes from the decision table below, checked top to bottom (the first match wins). Confidence comes from ADQS.</div>
  <div class="g c2"><div class="card"><h3>Decision table</h3><div id="aip-d"></div></div>
   <div class="card"><h3>Plan by horizon</h3><div class="hint">Asset count and current maintenance spend</div><div id="aip-h"></div></div></div>
  <div class="card" style="margin-top:14px"><h3>Ranked investment list</h3><div class="tw" id="aip-t"></div></div>
 </section>

 <section class="page" id="p-dci">
  <div class="note"><b>DCI (Data Completeness Index)</b> = average pass % across 19 database-wide checks (assets, meters, work orders, reference tables). This complements ADQS: ADQS scores each asset, DCI scores whole tables. Not filtered by slicers.</div>
  <div class="g kp" id="dci-k"></div>
  <div class="card" style="margin-top:14px"><h3>Checks: worst first</h3><div class="tw" id="dci-t"></div></div>
 </section>

 <section class="page" id="p-me">
  <div class="g c2" id="me"></div>
 </section>
</main></div>

<div class="dr" id="dr" role="dialog" aria-modal="true" aria-label="Asset calculation"><div class="drp" id="drp"></div></div>

<script>
const ASSETS=__ASSETS__, DCI=__DCI__;
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=v=>v===null||v===undefined||v===''?null:+v;
const f1=v=>num(v)===null?'n/a':num(v).toFixed(1);
const money=n=>(+n||0).toLocaleString(undefined,{maximumFractionDigits:0});
const avgOf=(a,k)=>{const s=a.map(x=>num(x[k])).filter(v=>v!==null);return s.length?s.reduce((t,v)=>t+v,0)/s.length:null;};
const sumOf=(a,k)=>a.reduce((t,x)=>t+(+x[k]||0),0);
const rag=v=>v===null?'#9ca3af':v>=80?'#16a34a':v>=60?'#d97706':v>=40?'#ea580c':'#dc2626';
const ragInv=v=>v===null?'#9ca3af':rag(100-v);
const C={Good:'#16a34a',Fair:'#d97706',Poor:'#ea580c',Critical:'#dc2626',High:'#dc2626',Medium:'#d97706',Low:'#16a34a',
 Trusted:'#16a34a',Usable:'#d97706','Fix data first':'#dc2626',Replace:'#dc2626','Refurbish / Overhaul':'#ea580c',
 'Increase inspection':'#d97706','Run & monitor':'#16a34a',Q1:'#dc2626',Q2:'#ea580c',Q3:'#d97706',Q4:'#16a34a'};
const CONF={High:'#16a34a',Medium:'#d97706',Low:'#dc2626'};
const pill=(t,c)=>`<span class="pill" style="background:${c||'#6b7280'}">${esc(t)}</span>`;
const sc=v=>`<b style="color:${rag(num(v))}">${f1(v)}</b>`, scI=v=>`<b style="color:${ragInv(num(v))}">${f1(v)}</b>`;
const QN={Q1:'Poor health + High criticality',Q2:'Poor health + Low criticality',Q3:'Good health + High criticality',Q4:'Good health + Low criticality'};
const QS={Q1:'Act now',Q2:'Fix cheaply / run to failure',Q3:'Protect',Q4:'Optimise'};
const QA={Q1:'Replace or refurbish in the Year-1 capital plan; monitor condition in the meantime; hold critical spares',
 Q2:'Corrective maintenance; defer capex; bundle into a volume replacement programme',
 Q3:'Keep PM / condition monitoring; secure spares; no capex needed',
 Q4:'Extend PM intervals; reduce inspections; move budget to Q1'};
const DIMS=['Completeness','Validity','Accuracy','Consistency','Timeliness'];
const RULES=[
 ['Completeness','C_Desc','Description filled'],['Completeness','C_Meter','Has at least one meter'],
 ['Completeness','C_Reading','Meter has readings'],['Completeness','C_PM','Covered by a PM'],['Completeness','C_WOHist','Has work order history'],
 ['Validity','V_Priority','Priority is 1–5'],['Validity','V_Status','Status in allowed list'],['Validity','V_Unit','Meter unit in allowed list'],
 ['Validity','V_Range','No negative readings'],['Validity','V_Dates','No future-dated readings / WOs'],
 ['Accuracy','A_LastRead','LASTREADING = latest measurement'],['Accuracy','A_Outlier','No outlier readings (> 3 SD)'],
 ['Accuracy','A_LabHrs','WO hours = labour actuals (±10%)'],
 ['Consistency','S_LocSite','Location belongs to asset site'],['Consistency','S_WOSite','WO site = asset site'],
 ['Consistency','S_WOLoc','WO location = asset location'],['Consistency','S_StatAct','Inactive asset has no open WO / PM'],
 ['Timeliness','T_Reading','Meter reading in last 180 days'],['Timeliness','T_PM','No PM overdue > 30 days'],['Timeliness','T_WO','Work order in last 365 days']];
const MAXCOST=Math.max(1,...ASSETS.map(a=>+a.MaintCost||0)), MAXCM=Math.max(1,...ASSETS.map(a=>+a.CorrectiveWO||0));

// ---------- helpers
function bars(el,rows,max,suffix=''){
 $(el).innerHTML=rows.map(r=>`<div class="bar"><div>${r.n}</div><div class="tr"><div class="fl" style="width:${max?Math.max(0,Math.min(100,100*r.v/max)):0}%;background:${r.c}"></div></div><div class="v">${r.t??(f1(r.v)+suffix)}</div></div>`).join('')||'<div class="hint">No data</div>';
}
function countBars(el,a,k,order,pal=C){const r=order.map(o=>({n:o,v:a.filter(x=>x[k]===o).length,c:pal[o]}));bars(el,r.map(x=>({...x,t:x.v})),Math.max(1,...r.map(x=>x.v)));}
function table(el,rows,cols,click=true){
 let sk=null,sd=1;
 const draw=()=>{const d=[...rows];if(sk)d.sort((x,y)=>{const a=x[sk],b=y[sk];return((a??-1e9)>(b??-1e9)?1:(a??-1e9)<(b??-1e9)?-1:0)*sd;});
  $(el).innerHTML=`<table><thead><tr>${cols.map(c=>`<th data-k="${c.k}">${c.h}${sk===c.k?(sd>0?' ▲':' ▼'):''}</th>`).join('')}</tr></thead><tbody>${
   d.map(r=>`<tr ${click&&r.ASSETNUM?`class="click" data-a="${esc(r.ASSETNUM)}" tabindex="0"`:''}>${cols.map(c=>`<td>${c.f?c.f(r[c.k],r):esc(r[c.k])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  $(el).querySelectorAll('th').forEach(th=>th.addEventListener('click',()=>{const k=th.dataset.k;sd=sk===k?-sd:1;sk=k;draw();}));
  $(el).querySelectorAll('tr.click').forEach(tr=>{tr.addEventListener('click',()=>openAsset(tr.dataset.a));tr.addEventListener('keydown',e=>{if(e.key==='Enter')openAsset(tr.dataset.a);});});};
 draw();
}
function kpi(label,val,target,status,color,fmt){
 return `<div class="card kpi" style="border-top-color:${color}"><div class="l">${label}</div><div class="v" style="color:${color}">${fmt??val}</div>
  <div class="t">${target}</div><div class="s" style="color:${color}">${status}</div></div>`;
}
function formula(el,parts,result,resLabel){
 $(el).innerHTML=parts.map((p,i)=>`${i?'<div class="op">+</div>':''}<div class="fx"><div class="w">${p.w}</div><div class="n">${p.n}</div><div class="c">${p.c}</div></div>`).join('')
  +`<div class="op">=</div><div class="fx res"><div class="w">${resLabel}</div><div class="n">average across filtered assets</div><div class="c" style="font-size:24px">${result}</div></div>`;
}
const status=(v,t=80)=>v===null?'No data':v>=t?'On target':v>=60?'Below target':'Action needed';

// ---------- filters
let A=ASSETS, quadSel=null;
function initFilters(){
 const opt=(id,vals)=>$(id).innerHTML='<option value="">All</option>'+vals.map(v=>`<option>${esc(v)}</option>`).join('');
 opt('fSite',[...new Set(ASSETS.map(a=>a.SITEID))].sort()); opt('fBand',['Good','Fair','Poor','Critical']); opt('fQuad',['Q1','Q2','Q3','Q4']);
 ['fSite','fBand','fQuad'].forEach(id=>$(id).addEventListener('change',render)); $('fText').addEventListener('input',render);
 $('fReset').addEventListener('click',()=>{['fSite','fBand','fQuad','fText'].forEach(id=>$(id).value='');quadSel=null;render();});
}
function applyFilters(){
 const s=$('fSite').value,b=$('fBand').value,q=$('fQuad').value,t=$('fText').value.trim().toLowerCase();
 A=ASSETS.filter(a=>(!s||a.SITEID===s)&&(!b||a.HealthBand===b)&&(!q||a.Quadrant===q)&&(!t||(a.ASSETNUM+' '+(a.DESCRIPTION||'')+' '+a.LOCATION).toLowerCase().includes(t)));
}

// ---------- pages
function pOverview(){
 const ahi=avgOf(A,'AHI'),aci=avgOf(A,'ACI'),adqs=avgOf(A,'ADQS'),q1=A.filter(a=>a.Quadrant==='Q1').length;
 const inv=A.filter(a=>a.Action==='Replace'||a.Action==='Refurbish / Overhaul'),dci=avgOf(DCI,'CompletenessPct');
 $('ov-k').innerHTML=kpi('Asset health (AHI)',f1(ahi),'Target ≥ 80',status(ahi),rag(ahi))
  +kpi('Data quality (ADQS)',f1(adqs),'Target ≥ 80',status(adqs),rag(adqs))
  +kpi('Avg criticality (ACI)',f1(aci),`${A.filter(a=>a.CriticalityBand==='High').length} high-criticality assets`,'Consequence of failure','#1f2937')
  +kpi('Q1: Act now',q1,'Poor health + high criticality',q1?`${(100*q1/Math.max(1,A.length)).toFixed(0)}% of assets`:'None',q1?'#dc2626':'#16a34a')
  +kpi('Investment candidates',inv.length,`Spend so far ${money(sumOf(inv,'MaintCost'))}`,'Replace / Refurbish',inv.length?'#ea580c':'#16a34a')
  +kpi('Data completeness (DCI)',f1(dci)+'%','Target ≥ 90%',dci>=90?'On target':'Below target',dci>=90?'#16a34a':rag(dci));
 // scatter
 const W=560,H=360,p=40,x=v=>p+(W-p-10)*v/100,y=v=>H-p-(H-p-10)*(100-v)/100;
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Risk matrix"><rect x="${x(60)}" y="${y(0)}" width="${x(100)-x(60)}" height="${y(60)-y(0)}" fill="#fee2e2"/>
 <text x="${x(99)}" y="${y(0)+14}" font-size="11" text-anchor="end" fill="#dc2626" font-weight="600">Q1 · Act now</text>
 <text x="${x(1)+4}" y="${y(100)+14}" font-size="11" fill="#16a34a" font-weight="600">Q4 · Optimise</text>
 <rect x="${p}" y="10" width="${W-p-10}" height="${H-p-10}" fill="none" stroke="#cbd5e1"/>
 <line x1="${x(60)}" y1="10" x2="${x(60)}" y2="${H-p}" stroke="#94a3b8" stroke-dasharray="4 3"/><line x1="${p}" y1="${y(60)}" x2="${W-10}" y2="${y(60)}" stroke="#94a3b8" stroke-dasharray="4 3"/>`;
 for(const t of [0,20,40,60,80,100])s+=`<text x="${x(t)}" y="${H-p+15}" font-size="10" text-anchor="middle" fill="#6b7280">${t}</text><text x="${p-5}" y="${y(t)+3}" font-size="10" text-anchor="end" fill="#6b7280">${t}</text>`;
 s+=`<text x="${(W+p)/2}" y="${H-4}" font-size="11" text-anchor="middle" fill="#374151">ACI → more critical</text><text transform="translate(11,${H/2}) rotate(-90)" font-size="11" text-anchor="middle" fill="#374151">AHI ↓ worse health</text>`;
 A.forEach(a=>s+=`<circle class="dot" data-a="${esc(a.ASSETNUM)}" cx="${x(+a.ACI)}" cy="${y(+a.AHI)}" r="5.5" fill="${C[a.Quadrant]}" fill-opacity=".8" stroke="#fff" style="cursor:pointer"><title>${esc(a.ASSETNUM)} ${esc(a.DESCRIPTION||'')}  AHI ${a.AHI} · ACI ${a.ACI} · Risk ${a.RiskScore}</title></circle>`);
 $('ov-m').innerHTML=s+'</svg>'; $('ov-m').querySelectorAll('.dot').forEach(d=>d.addEventListener('click',()=>openAsset(d.dataset.a)));
 bars('ov-q',['Q1','Q2','Q3','Q4'].map(q=>{const v=A.filter(a=>a.Quadrant===q).length;return{n:`${q} · ${QS[q]}`,v,t:v,c:C[q]};}),Math.max(1,A.length));
 countBars('ov-h',A,'HealthBand',['Good','Fair','Poor','Critical']);
 table('ov-t',[...A].sort((a,b)=>b.RiskScore-a.RiskScore).slice(0,10),[{k:'RiskRank',h:'#'},{k:'ASSETNUM',h:'Asset'},{k:'DESCRIPTION',h:'Description'},{k:'SITEID',h:'Site'},
  {k:'AHI',h:'AHI',f:sc},{k:'ACI',h:'ACI',f:scI},{k:'RiskScore',h:'Risk',f:v=>`<b>${f1(v)}</b>`},{k:'Quadrant',h:'Quad',f:v=>pill(v,C[v])},{k:'Action',h:'Action',f:v=>pill(v,C[v])},{k:'Confidence',h:'Data confidence',f:v=>pill(v,CONF[v])}]);
}
function pDQ(){
 const dv=DIMS.map(d=>avgOf(A,d)), valid=dv.filter(v=>v!==null);
 formula('dq-f',DIMS.map((d,i)=>({w:d+' · 1/5',n:`${RULES.filter(r=>r[0]===d).length} rules`,c:`<span style="color:${rag(dv[i])}">${f1(dv[i])}</span>`})),
   f1(avgOf(A,'ADQS')),'ADQS');
 const rr=RULES.map(([d,k,n])=>{const ap=A.filter(a=>num(a[k])!==null),fail=ap.filter(a=>num(a[k])===0).length;
   return{Dimension:d,Rule:n,Checked:ap.length,Failing:fail,Pass:ap.length?100*(ap.length-fail)/ap.length:null};}).sort((a,b)=>(a.Pass??101)-(b.Pass??101));
 table('dq-r',rr,[{k:'Dimension',h:'Dimension'},{k:'Rule',h:'Rule'},{k:'Checked',h:'Assets checked'},{k:'Failing',h:'Failing',f:v=>v?`<b class="ko">${v}</b>`:'0'},
  {k:'Pass',h:'Pass %',f:v=>v===null?'<span class="na">n/a</span>':`<div style="display:flex;gap:6px;align-items:center"><div style="width:60px;height:8px;background:#eef1f5;border-radius:3px"><div style="width:${v}%;height:100%;background:${rag(v)};border-radius:3px"></div></div><b style="color:${rag(v)}">${v.toFixed(0)}%</b></div>`}],false);
 countBars('dq-b',A,'ADQSBand',['Trusted','Usable','Fix data first']); countBars('dq-c',A,'Confidence',['High','Medium','Low'],CONF);
 const dc=v=>num(v)===null?'<span class="na">n/a</span>':sc(v);
 table('dq-t',[...A].sort((a,b)=>a.ADQS-b.ADQS).map(a=>({...a,Fails:RULES.filter(r=>num(a[r[1]])===0).map(r=>r[2]).join('; ')})),[{k:'ASSETNUM',h:'Asset'},{k:'SITEID',h:'Site'},{k:'ADQS',h:'ADQS',f:sc},
  {k:'ADQSBand',h:'Band',f:v=>pill(v,C[v])},...DIMS.map(d=>({k:d,h:d.slice(0,6)+'.',f:dc})),{k:'Fails',h:'Failed rules',f:v=>`<span style="font-size:12px">${esc(v)||'—'}</span>`}]);
}
function pAHI(){
 const D=[['FailureScore','Failure history',.35,'100 − 15 per CM/EM work order'],['PMScore','PM compliance',.25,'% of PMs not overdue (no PM = 50)'],
  ['ConditionScore','Condition',.25,'100 − % gap of latest meter reading vs its average (no data = 50)'],['BacklogScore','Backlog',.15,'100 − 10 per open work order']];
 formula('ahi-f',D.map(([k,n,w,h])=>{const v=avgOf(A,k);return{w:`${n} · ${w*100}%`,n:`<span title="${h}">${h}</span>`,c:`${f1(v)} × ${w} = <span style="color:${rag(v)}">${v===null?'n/a':(v*w).toFixed(1)}</span>`};}),f1(avgOf(A,'AHI')),'AHI');
 const lost=D.map(([k,n,w])=>{const v=avgOf(A,k);return{n:`${n} (${w*100}%)`,v:v===null?0:w*(100-v),c:'#ea580c'};});
 bars('ahi-l',lost.map(x=>({...x,t:'−'+x.v.toFixed(1)})),Math.max(1,...lost.map(x=>x.v)));
 countBars('ahi-b',A,'HealthBand',['Good','Fair','Poor','Critical']);
 table('ahi-t',[...A].sort((a,b)=>a.AHI-b.AHI),[{k:'ASSETNUM',h:'Asset'},{k:'DESCRIPTION',h:'Description'},{k:'SITEID',h:'Site'},
  {k:'CorrectiveWO',h:'CM/EM WOs'},{k:'FailureScore',h:'Failure',f:sc},{k:'PMOverdue',h:'PM overdue',f:(v,r)=>`${v} of ${r.PMCount}`},{k:'PMScore',h:'PM',f:sc},
  {k:'ConditionScore',h:'Condition',f:sc},{k:'OpenWO',h:'Open WOs'},{k:'BacklogScore',h:'Backlog',f:sc},{k:'AHI',h:'AHI',f:sc},{k:'HealthBand',h:'Band',f:v=>pill(v,C[v])}]);
}
function pACI(){
 const D=[['PriorityScore','Priority',.5,'Priority 1 = 100, 2 = 80 … 5 = 20'],['CostScore','Maintenance cost',.3,`Asset cost ÷ highest cost (${money(MAXCOST)}) × 100`],
  ['FrequencyScore','Failure frequency',.2,`CM/EM WOs ÷ highest count (${MAXCM}) × 100`]];
 formula('aci-f',D.map(([k,n,w,h])=>{const v=avgOf(A,k);return{w:`${n} · ${w*100}%`,n:h,c:`${f1(v)} × ${w} = <span style="color:${ragInv(v)}">${v===null?'n/a':(v*w).toFixed(1)}</span>`};}),f1(avgOf(A,'ACI')),'ACI');
 countBars('aci-b',A,'CriticalityBand',['High','Medium','Low']);
 const cb=['High','Medium','Low'].map(k=>({n:k,v:sumOf(A.filter(a=>a.CriticalityBand===k),'MaintCost'),c:C[k]}));
 bars('aci-c',cb.map(x=>({...x,t:money(x.v)})),Math.max(1,...cb.map(x=>x.v)));
 table('aci-t',[...A].sort((a,b)=>b.ACI-a.ACI),[{k:'ASSETNUM',h:'Asset'},{k:'DESCRIPTION',h:'Description'},{k:'SITEID',h:'Site'},
  {k:'PRIORITY',h:'Priority'},{k:'PriorityScore',h:'Priority pts',f:scI},{k:'MaintCost',h:'Maint cost',f:money},{k:'CostScore',h:'Cost pts',f:scI},
  {k:'CorrectiveWO',h:'CM/EM'},{k:'FrequencyScore',h:'Freq pts',f:scI},{k:'ACI',h:'ACI',f:scI},{k:'CriticalityBand',h:'Band',f:v=>pill(v,C[v])}]);
}
function pHC(){
 const bg={Q1:'#fee2e2',Q2:'#ffedd5',Q3:'#fef3c7',Q4:'#dcfce7'};
 const box=q=>{const g=A.filter(a=>a.Quadrant===q),fl=g.filter(a=>a.DataFlag&&a.DataFlag!=='OK').length;
  return `<div class="qd${quadSel===q?' sel':''}" data-q="${q}" role="button" tabindex="0" style="background:${bg[q]}"><h4>${q} · ${QN[q]}</h4><b>${QS[q]}</b>
  <div class="big">${g.length} <span style="font-size:13px;font-weight:400">assets</span></div>
  <div style="font-size:12px">Spend ${money(sumOf(g,'MaintCost'))} · avg AHI ${f1(avgOf(g,'AHI'))} · avg ACI ${f1(avgOf(g,'ACI'))}${fl?` · <b>${fl} need data check</b>`:''}</div><div class="ap">${QA[q]}</div></div>`;};
 $('hc-g').innerHTML=`<div class="ax ay">High criticality (ACI ≥ 60)</div>${box('Q3')}${box('Q1')}<div class="ax ay">Low criticality</div>${box('Q4')}${box('Q2')}<div></div><div class="ax">Good health (AHI ≥ 60)</div><div class="ax">Poor health (AHI &lt; 60)</div>`;
 $('hc-g').querySelectorAll('.qd').forEach(el=>{const go=()=>{quadSel=quadSel===el.dataset.q?null:el.dataset.q;pHC();};el.addEventListener('click',go);el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});});
 table('hc-t',A.filter(a=>!quadSel||a.Quadrant===quadSel).sort((a,b)=>a.Quadrant.localeCompare(b.Quadrant)||a.AHI-b.AHI),[{k:'Quadrant',h:'Quad',f:v=>pill(v,C[v])},
  {k:'ASSETNUM',h:'Asset'},{k:'DESCRIPTION',h:'Description'},{k:'SITEID',h:'Site'},{k:'AHI',h:'AHI',f:sc},{k:'ACI',h:'ACI',f:scI},{k:'ADQS',h:'ADQS',f:sc},
  {k:'Strategy',h:'Strategy'},{k:'MaintCost',h:'Maint cost',f:money},{k:'DataFlag',h:'Data',f:v=>v==='OK'?pill('OK','#16a34a'):pill('Verify data','#dc2626')}]);
}
function pAIP(){
 const rows=[['1','AHI < 40 and ACI ≥ 60','Replace','Year 1'],['2','AHI < 60','Refurbish / Overhaul','Year 1-2'],['3','AHI < 80 and ACI ≥ 60','Increase inspection','Year 2-3'],['4','Everything else','Run & monitor','Year 3-5']]
  .map(([o,c,a,h])=>({Order:o,Condition:c,Action:a,Horizon:h,Assets:A.filter(x=>x.Action===a).length,Spend:sumOf(A.filter(x=>x.Action===a),'MaintCost')}));
 table('aip-d',rows,[{k:'Order',h:'#'},{k:'Condition',h:'If'},{k:'Action',h:'Then',f:v=>pill(v,C[v])},{k:'Horizon',h:'When'},{k:'Assets',h:'Assets'},{k:'Spend',h:'Spend',f:money}],false);
 const hz=['Year 1','Year 1-2','Year 2-3','Year 3-5'].map((k,i)=>{const g=A.filter(a=>a.Horizon===k);return{n:`${k} · ${g.length} assets`,v:sumOf(g,'MaintCost'),c:['#dc2626','#ea580c','#d97706','#16a34a'][i]};});
 bars('aip-h',hz.map(x=>({...x,t:money(x.v)})),Math.max(1,...hz.map(x=>x.v)));
 table('aip-t',[...A].sort((a,b)=>b.RiskScore-a.RiskScore),[{k:'RiskRank',h:'Rank'},{k:'ASSETNUM',h:'Asset'},{k:'DESCRIPTION',h:'Description'},{k:'SITEID',h:'Site'},
  {k:'AHI',h:'AHI',f:sc},{k:'ACI',h:'ACI',f:scI},{k:'RiskScore',h:'Risk',f:(v,r)=>`<b>${f1(v)}</b> <span class="na" style="font-size:11px">(${(100-r.AHI).toFixed(1)}×${f1(r.ACI)}÷100)</span>`},
  {k:'Action',h:'Action',f:v=>pill(v,C[v])},{k:'Horizon',h:'Horizon'},{k:'MaintCost',h:'Maint cost',f:money},{k:'Confidence',h:'Confidence',f:v=>pill(v,CONF[v])}]);
}
function pDCI(){
 const all=avgOf(DCI,'CompletenessPct'),areas=[...new Set(DCI.map(d=>d.Area))];
 $('dci-k').innerHTML=kpi('DCI overall',f1(all)+'%','Target ≥ 90%',all>=90?'On target':'Below target',all>=90?'#16a34a':rag(all))
  +areas.map(ar=>{const v=avgOf(DCI.filter(d=>d.Area===ar),'CompletenessPct');return kpi(ar,f1(v)+'%',`${DCI.filter(d=>d.Area===ar).length} checks`,v>=90?'On target':'Below target',v>=90?'#16a34a':rag(v));}).join('');
 table('dci-t',[...DCI].sort((a,b)=>(a.CompletenessPct??101)-(b.CompletenessPct??101)),[{k:'Area',h:'Area'},{k:'CheckName',h:'Check'},{k:'TotalRows',h:'Rows checked'},
  {k:'FailRows',h:'Failing rows',f:v=>v?`<b class="ko">${v}</b>`:'0'},{k:'CompletenessPct',h:'Complete %',f:v=>num(v)===null?'<span class="na">n/a</span>':sc(v)}],false);
}
function pMethod(){
 const card=(t,b)=>`<div class="card"><h3>${t}</h3><div style="font-size:13px;line-height:1.6">${b}</div></div>`;
 $('me').innerHTML=
  card('ADQS: Asset Data Quality Score','Average of 5 dimensions (DAMA data-quality dimensions). Each dimension = rules passed ÷ rules that apply × 100.<br>'+DIMS.map(d=>`<b>${d}</b>: `+RULES.filter(r=>r[0]===d).map(r=>r[2]).join(' · ')).join('<br>')+'<br>Bands: ≥80 Trusted · 60–79 Usable · &lt;60 Fix data first.<br><i>Accuracy here is a system proxy. True accuracy needs field verification.</i>')
 +card('AHI: Asset Health Index','35% Failure history (100 − 15 × CM/EM WOs)<br>25% PM compliance (% of PMs not overdue; no PM = 50)<br>25% Condition (100 − % gap between latest meter reading and its average; no data = 50)<br>15% Backlog (100 − 10 × open WOs)<br>Bands: ≥80 Good · 60–79 Fair · 40–59 Poor · &lt;40 Critical.')
 +card('ACI: Asset Criticality Index','50% Priority (1 → 100 … 5 → 20)<br>30% Maintenance cost ÷ highest-cost asset × 100<br>20% CM/EM WOs ÷ highest count × 100<br>Bands: ≥60 High · 40–59 Medium · &lt;40 Low.')
 +card('Health × Criticality','Q1 Poor + High → Act now · Q2 Poor + Low → Fix cheaply / run to failure · Q3 Good + High → Protect · Q4 Good + Low → Optimise.<br>Poor = AHI &lt; 60; High = ACI ≥ 60. Assets with ADQS &lt; 60 are flagged "Verify data before acting".')
 +card('AIP: Asset Investment Planning','Risk = (100 − AHI) × ACI ÷ 100 (likelihood × consequence, same idea as CNAIM health/criticality/risk indices).<br>Decision table: Replace (Y1) → Refurbish (Y1-2) → Increase inspection (Y2-3) → Run & monitor (Y3-5). Confidence: ADQS ≥80 High · ≥60 Medium · else Low.')
 +card('DCI: Data Completeness Index','Average pass % of 19 table-level checks across ASSET, ASSETMETER, MEASUREMENT, WORKORDER, LABTRANS, WOSTATUS, LOCATIONS, ITEM, JOBPLAN, PERSON. Target ≥ 90%.')
 +card('Data sources & SQL views','lab.* tables → apm.vw_AssetBase → vw_AHI, vw_ACI, vw_DQ_Asset (rules) → vw_ADQS → vw_AIP, vw_HC_Quadrant; vw_DCI. Script: apm_all_in_one.sql.')
 +card('Report design','Built on the 3-30-300 rule: headline KPIs with targets top-left (3 s), slicers to filter (30 s), click any asset for step-by-step details (300 s).');
}

// ---------- drill-through
function openAsset(id){
 const a=ASSETS.find(x=>x.ASSETNUM===id); if(!a)return;
 const row=(l,f,v,b)=>`<tr${b?' class="tot"':''}><td>${l}</td><td class="na">${f}</td><td class="r">${v}</td></tr>`;
 const w=(s,k)=>(+a[s]*k).toFixed(1);
 const ahi=`<table class="calc">${row('Failure history',`${a.CorrectiveWO} CM/EM WOs → 100 − ${a.CorrectiveWO}×15 = ${f1(a.FailureScore)} × 35%`,w('FailureScore',.35))
  +row('PM compliance',a.PMCount?`${a.PMCount-a.PMOverdue} of ${a.PMCount} PMs on time = ${f1(a.PMScore)} × 25%`:`no PM → 50 × 25%`,w('PMScore',.25))
  +row('Condition',`meter condition score ${f1(a.ConditionScore)} × 25%`,w('ConditionScore',.25))
  +row('Backlog',`${a.OpenWO} open WOs → 100 − ${a.OpenWO}×10 = ${f1(a.BacklogScore)} × 15%`,w('BacklogScore',.15))
  +row('AHI','',`<span style="color:${rag(+a.AHI)}">${f1(a.AHI)} · ${esc(a.HealthBand)}</span>`,1)}</table>`;
 const aci=`<table class="calc">${row('Priority',`priority ${a.PRIORITY} → ${f1(a.PriorityScore)} × 50%`,w('PriorityScore',.5))
  +row('Maintenance cost',`${money(a.MaintCost)} ÷ ${money(MAXCOST)} = ${f1(a.CostScore)} × 30%`,w('CostScore',.3))
  +row('Failure frequency',`${a.CorrectiveWO} ÷ ${MAXCM} = ${f1(a.FrequencyScore)} × 20%`,w('FrequencyScore',.2))
  +row('ACI','',`<span style="color:${ragInv(+a.ACI)}">${f1(a.ACI)} · ${esc(a.CriticalityBand)}</span>`,1)}</table>`;
 const dq=DIMS.map(d=>{const rs=RULES.filter(r=>r[0]===d),ap=rs.filter(r=>num(a[r[1]])!==null),ps=ap.filter(r=>num(a[r[1]])===1).length;
  return `<tr class="tot"><td>${d}</td><td class="na">${ap.length?`${ps} of ${ap.length} rules passed`:'no rules apply'}</td><td class="r" style="color:${rag(num(a[d]))}">${f1(a[d])}</td></tr>`
   +rs.map(r=>{const v=num(a[r[1]]);return `<tr><td style="padding-left:14px">${v===null?'<span class="na">–</span>':v?'<span class="ok">✔</span>':'<span class="ko">✖</span>'} ${r[2]}</td><td class="na">${v===null?'not applicable':''}</td><td></td></tr>`;}).join('');}).join('');
 const dv=DIMS.map(d=>num(a[d])).filter(v=>v!==null);
 $('drp').innerHTML=`<div style="display:flex;justify-content:space-between;align-items:start;gap:8px"><div><h2>${esc(a.ASSETNUM)} · ${esc(a.DESCRIPTION||'(no description)')}</h2>
  <div class="na">${esc(a.SITEID)} › ${esc(a.LOCATION)} · Risk rank #${a.RiskRank}</div></div><button class="btn" type="button" id="drx">✕ Close</button></div>
  <div style="margin-top:10px">${pill(a.Quadrant+' · '+QS[a.Quadrant],C[a.Quadrant])} ${pill(a.Action+' · '+a.Horizon,C[a.Action])} ${pill('Confidence '+a.Confidence,CONF[a.Confidence])}</div>
  <h4>1 · Health (AHI)</h4>${ahi}<h4>2 · Criticality (ACI)</h4>${aci}
  <h4>3 · Risk & decision</h4><table class="calc">${row('Risk','(100 − AHI) × ACI ÷ 100',`(${(100-a.AHI).toFixed(1)} × ${f1(a.ACI)}) ÷ 100 = <b>${f1(a.RiskScore)}</b>`)}
   ${row('Quadrant',`AHI ${a.AHI<60?'< 60 (poor)':'≥ 60 (good)'} · ACI ${a.ACI>=60?'≥ 60 (high)':'< 60 (low)'}`,a.Quadrant)}
   ${row('Action',QA[a.Quadrant],esc(a.Action))}</table>
  <h4>4 · Data quality (ADQS)</h4><table class="calc">${dq}${row('ADQS',`average of ${dv.length} dimensions`,`<span style="color:${rag(+a.ADQS)}">${f1(a.ADQS)} · ${esc(a.ADQSBand)}</span>`,1)}</table>`;
 $('dr').classList.add('on'); $('drx').focus(); $('drx').addEventListener('click',closeAsset);
}
function closeAsset(){$('dr').classList.remove('on');}
$('dr').addEventListener('click',e=>{if(e.target.id==='dr')closeAsset();});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeAsset();});

// ---------- navigation
const PAGES=[['ov','Overview','Where do we stand, and which assets need action?',pOverview],['dq','Data Quality (ADQS)','Can we trust the data behind the decisions?',pDQ],
 ['ahi','Asset Health (AHI)','How healthy are the assets, and what is pulling health down?',pAHI],['aci','Criticality (ACI)','Which assets matter most if they fail?',pACI],
 ['hc','Health × Criticality','Which strategy applies to each asset?',pHC],['aip','Investment Plan (AIP)','Where should money go first, and when?',pAIP],
 ['dci','Data Completeness (DCI)','How complete are the source tables?',pDCI],['me','Methodology','How every number is calculated',pMethod]];
let cur='ov';
$('nav').insertAdjacentHTML('beforeend',PAGES.map(([k,n])=>`<button type="button" data-p="${k}">${n}</button>`).join(''));
$('nav').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{cur=b.dataset.p;render();}));
function render(){
 applyFilters(); const pg=PAGES.find(p=>p[0]===cur);
 $('ptitle').textContent=pg[1]; $('pq').textContent=pg[2]+`  ·  ${A.length} of ${ASSETS.length} assets`;
 document.querySelectorAll('.page').forEach(s=>s.classList.toggle('on',s.id==='p-'+cur));
 $('nav').querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.p===cur));
 pg[3]();
}
initFilters(); render();
</script></body></html>"""


def build(assets, dci, source):
    html = (HTML.replace("__ASSETS__", json.dumps(assets)).replace("__DCI__", json.dumps(dci))
                .replace("__SOURCE__", source).replace("__BUILT__", datetime.datetime.now().strftime("%d %b %Y %H:%M")))
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report written: {OUT_FILE}  ({len(assets)} assets, {len(dci)} DCI checks)")


if __name__ == "__main__":
    demo = "--demo" in sys.argv
    assets, dci = load_demo() if demo else load_live()
    build(assets, dci, "DEMO sample data" if demo else f"{SERVER} / {DATABASE}")
    if "--no-open" not in sys.argv:
        webbrowser.open("file:///" + OUT_FILE.replace("\\", "/"))
