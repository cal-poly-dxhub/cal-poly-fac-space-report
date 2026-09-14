#!/usr/bin/env python3
"""Compare our CSV against the Planon-generated report, column by column."""
import csv, re, io, sys, collections, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report as B

HERE = os.path.dirname(os.path.abspath(__file__))
PLANON = os.path.join(os.path.dirname(HERE), "planon-reference.csv")
SLO = ("00", "01", "02", "03", "SC-00", "SC-01")   # all six CSU centers

def planon_ref_date(path=PLANON):
    """The reference date Planon stamped into its own section headers."""
    raw = open(path, "rb").read().decode("cp1252")
    for row in csv.reader(io.StringIO(raw)):
        m = row and re.match(r"^(\d{4}-\d{2}-\d{2})\s*:", row[0].strip())
        if m:
            return m.group(1)
    raise SystemExit(f"no reference date found in {path}")


def load_planon(path=PLANON, centers=SLO):
    raw = open(path, "rb").read().decode("cp1252")
    out, center = {}, None
    for r in csv.reader(io.StringIO(raw)):
        if not any(x.strip() for x in r):
            continue
        first = r[0].strip()
        m = re.match(r"^\d{4}-\d{2}-\d{2}\s*:\s*(\S+)\s*-\s*(.*)$", first)
        if m:
            center = m.group(1); continue
        if first == "NUM":
            continue
        if center in centers:
            out[(center, first, r[1].strip())] = dict(
                name=r[2].strip(), catc=r[3].strip(), stac=r[5].strip(),
                ownc=r[7].strip(), gsf=r[9].strip(), asf=r[10].strip(),
                compl=r[12].strip())
    return out

def main():
    planon = load_planon()
    root = os.path.join(os.path.dirname(HERE), "source_data")
    ref = planon_ref_date()
    mine = {(x["CENTER CODE"], x["FAC NUM"], x["SFX"]): x
            for x in B.Report(B.LocalSource(root), ref, list(SLO)).build()}
    print(f"  reference date {ref} (read from Planon's section headers)")
    print(f"  planon {len(planon)} rows    ours {len(mine)} rows")
    only_p = sorted(set(planon) - set(mine))
    only_m = sorted(set(mine) - set(planon))
    if only_p: print(f"  in planon only: {only_p}")
    if only_m: print(f"  in ours only  : {only_m}")

    both = sorted(set(planon) & set(mine))
    hit = collections.Counter(); ex = collections.defaultdict(list)
    for k in both:
        p, m = planon[k], mine[k]
        def chk(f, a, b):
            if str(a) == str(b): hit[f] += 1
            else: ex[f].append((k, a, b))
        chk("FAC NAME", p["name"], m["FAC NAME"])
        chk("CATEGORY", p["catc"], m["CATEGORY CODE"])
        chk("STATUS",   p["stac"], m["STATUS CODE"])
        chk("OWNER",    p["ownc"], m["OWNER CODE"])
        chk("GSF",      p["gsf"],  "" if m["GSF"] is None else m["GSF"])
        chk("ASF",      p["asf"],  "" if m["ASF"] is None else m["ASF"])
        mm, yy = (p["compl"].split("-") + [""])[:2]
        chk("COMPL DATE", f"{yy}-{mm}", m["COMPL DATE"][:7])

    print(f"\n  {'column':<12} {'match':>8} / {len(both)}")
    for f in ["FAC NAME", "CATEGORY", "STATUS", "OWNER", "GSF", "ASF", "COMPL DATE"]:
        flag = "" if hit[f] == len(both) else "   MISMATCH"
        print(f"  {f:<12} {hit[f]:>8} / {len(both)}{flag}")
    for f, items in ex.items():
        print(f"\n  {f} differences:")
        for k, a, b in items:
            print(f"    {k[0]:<6} {k[1]}-{k[2]:<3} planon={a!r}  ours={b!r}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
