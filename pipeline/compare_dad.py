#!/usr/bin/env python3
"""Compare our CSV against Planon's own aggregation output, read live from OData.

DataAggregationData is what Planon's report renders from, so this compares
against the source of their report rather than an emailed export. Rows carrying
'Has data' in FreeString13 are the ones that print.
"""
import csv, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report as B

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def main(dad_path="/tmp/dad2.csv", run="33", ref_date="2026-09-10"):
    src = B.LocalSource(os.path.join(ROOT, "source_data"))
    prop = {r["Syscode"]: r for r in src.table("Property")}
    codes = {r["Syscode"]: r for r in src.table("BaseCodes")}
    code = lambda s: (codes.get(s or "") or {}).get("Code", "")

    planon = {}
    for r in csv.DictReader(open(dad_path)):
        if r["DataAggregationDefinitionRef"] != run: continue
        if not (r.get("FreeString13") or "").strip(): continue
        anchor = prop.get(r.get("FreeString12") or "")
        if anchor is None: continue
        num, sfx = B.split_code(anchor.get("Code") or "")
        planon[(code(r.get("FreeString20")), num, sfx)] = dict(
            name=r.get("FreeString31") or "", cat=code(r.get("FreeString16")),
            sta=code(r.get("FreeString17")), own=code(r.get("FreeString18")),
            gsf=r.get("Area14") or "", asf=r.get("Area15") or "",
            compl=(r.get("FreeString19") or "")[:10])

    mine = {(x["CENTER CODE"], x["FAC NUM"], x["SFX"]): x
            for x in B.Report(src, ref_date).build()}
    print(f"  planon run {run}: {len(planon)} facilities    ours: {len(mine)}")
    for label, diff in (("in planon only", set(planon) - set(mine)),
                        ("in ours only",   set(mine) - set(planon))):
        if diff: print(f"  {label}: {sorted(diff)}")

    both = sorted(set(planon) & set(mine))
    hit = collections.Counter(); ex = collections.defaultdict(list)
    for k in both:
        p, m = planon[k], mine[k]
        def chk(f, a, b):
            if str(a) == str(b): hit[f] += 1
            else: ex[f].append((k, a, b))
        num = lambda v: "" if not v else str(int(float(v)))
        chk("FAC NAME", p["name"], m["FAC NAME"])
        chk("CATEGORY", p["cat"], m["CATEGORY CODE"])
        chk("STATUS",   p["sta"], m["STATUS CODE"])
        chk("OWNER",    p["own"], m["OWNER CODE"])
        chk("GSF", num(p["gsf"]), "" if m["GSF"] is None else m["GSF"])
        chk("ASF", num(p["asf"]), "" if m["ASF"] is None else m["ASF"])
        chk("COMPL DATE", p["compl"], m["COMPL DATE"])
    print(f"\n  {'column':<12} {'match':>8} / {len(both)}")
    for f in ["FAC NAME","CATEGORY","STATUS","OWNER","GSF","ASF","COMPL DATE"]:
        print(f"  {f:<12} {hit[f]:>8} / {len(both)}" + ("" if hit[f]==len(both) else "   MISMATCH"))
    for f, items in ex.items():
        print(f"\n  {f}:")
        for k, a, b in items[:6]:
            print(f"    {k[0]:<6} {k[1]}-{k[2]:<3} planon={a!r}  ours={b!r}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
