#!/usr/bin/env python3
"""Build the CO Facility Report CSV from Planon data.

Reimplements the aggregation that Planon's DAM module performed, following the
paths documented in ../report-column-paths.md and the step order in
../report-algorithm.md.

    python3 build_report.py --ref-date 2026-09-10

Check the output against Planon's own export with compare.py.

Data comes from a DataSource. Today that is LocalSource, reading the CSV mirror
in ../source_data/. ODataSource is the seam where the live Planon calls go; the
pipeline below does not care which it gets.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Tables the report needs. Space is deliberately absent: SpaceUsage carries its
# own PropertyRef, which agrees with Space.PropertyRef on every row.
TABLES = ["Property", "PropertyDetails", "SpaceUsage", "SpaceStandard", "BaseCodes"]

# All six CSU centers. Confirmed by the stakeholder: include every one, and do
# not drop properties whose center is blank.
CSU_CENTERS = ["00", "01", "02", "03", "SC-00", "SC-01"]

# Planon's own export header. Area 14 and Area 15 are the DAD slot names it
# uses for GSF and ASF; keeping them means the two files diff cleanly.
COLUMNS = [
    "NUM", "SFX", "FAC NAME",
    "CATEGORY CODE", "CATEGORY DESC",
    "STATUS CODE", "STATUS DESC",
    "OWNER CODE", "OWNER DESC",
    "Area 14", "Area 15", "EFFC", "COMPL DATE",
]


# ---------------------------------------------------------------- data sources

class DataSource:
    """Returns whole Planon tables as lists of dicts, keyed by OData names."""

    def table(self, name: str) -> list[dict]:
        raise NotImplementedError


class LocalSource(DataSource):
    """Reads the CSV mirror in source_data/. Stands in for live OData calls."""

    def __init__(self, root: str):
        self.root = root
        self._cache: dict[str, list[dict]] = {}

    def table(self, name: str) -> list[dict]:
        if name not in self._cache:
            path = os.path.join(self.root, name, f"{name}.csv")
            if not os.path.exists(path):
                raise FileNotFoundError(f"no mirror for {name} at {path}")
            with open(path, newline="", encoding="utf-8") as handle:
                self._cache[name] = list(csv.DictReader(handle))
        return self._cache[name]


class ODataSource(DataSource):
    """Live Planon. Not wired up yet; LocalSource is the current stand-in.

    When this lands it needs the paging fix from connector/planon_odata.py:
    sending $top makes the service omit @odata.nextLink and silently truncate to
    one page.
    """

    def table(self, name: str) -> list[dict]:
        raise NotImplementedError("live OData not wired up yet; use LocalSource")


# --------------------------------------------------------------------- helpers

def num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def effective(row: dict, ref_date: str) -> bool:
    """Is this row the version in force on ref_date?

    Planon keeps every version of a time-dependent record, so "how big is this
    building" has no answer without a date. Both bounds are inclusive and an
    empty EndDate means still in force, per the migration requirements.

    There is deliberately no "just give me the newest" mode. That answers a
    different question, and Planon's own runs are frequently backdated: report
    23 was generated in Feb 2025 against a Dec 2023 reference date.
    """
    begin = (row.get("BeginDate") or "")[:10]
    end = (row.get("EndDate") or "")[:10]
    if begin and begin > ref_date:
        return False
    if end and end < ref_date:
        return False
    return True


CODE = re.compile(r"^(?:(?P<campus>[A-Z]{2,})-)?(?P<num>\d{3})(?:-(?P<sfx>\w))?$")


def split_code(code: str) -> tuple[str, str]:
    """Building code -> (FAC NUM, SFX).

        008-A     -> ('008', 'A')
        008-0     -> ('008', '-')     a 0 suffix prints as a dash
        016       -> ('016', '-')     no suffix at all prints as a dash
        SC-024-0  -> ('024', '-')     Solano campus prefix is dropped

    The campus prefix is dropped because Planon's own report prints SC-024-0 as
    facility 024. That means NUM and SFX alone do not identify a facility across
    the whole report: SLO's Food Processing and Solano's Staff Housing #4 are
    both 024. The center is what separates them.
    """
    m = CODE.match(code.strip())
    if not m:
        return code[:3], "-"
    sfx = m.group("sfx")
    return m.group("num"), ("-" if (sfx is None or sfx == "0") else sfx)


# -------------------------------------------------------------------- pipeline

class Report:
    def __init__(self, source: DataSource, ref_date: str,
                 centers: list[str] | None = None, drop_archived: bool = False):
        self.source = source
        self.ref_date = ref_date
        # Retained only so callers can name the centers they expect to see. It
        # is not used to include or exclude anything.
        self.centers = centers or []
        self.drop_archived = drop_archived
        self.problems: list[str] = []

    def note(self, message: str) -> None:
        self.problems.append(message)

    # Step 1
    def load(self) -> None:
        self.property = {r["Syscode"]: r for r in self.source.table("Property")}
        self.details = self.source.table("PropertyDetails")
        self.usage = self.source.table("SpaceUsage")
        self.standard = {r["Syscode"]: r for r in self.source.table("SpaceStandard")}
        self.codes = {r["Syscode"]: r for r in self.source.table("BaseCodes")}

    # Step 2
    def resolve(self, syscode: str | None, expect_group: str | None = None):
        """syscode -> (Code, Name) from BaseCodes."""
        row = self.codes.get(syscode or "")
        if row is None:
            if syscode:
                self.note(f"syscode {syscode} not found in BaseCodes")
            return "", ""
        if expect_group and row.get("GroupCode") != expect_group:
            self.note(
                f"syscode {syscode} is in group {row.get('GroupCode')}, "
                f"expected {expect_group}"
            )
        return row.get("Code") or "", row.get("Name") or ""

    # Step 3
    def included_details(self) -> list[dict]:
        yes_syscodes = {
            s for s, r in self.codes.items()
            if r.get("GroupCode") == "YESNO" and r.get("Code") == "Y"
        }
        if not yes_syscodes:
            self.note("no YESNO/Y row found in BaseCodes; inclusion filter is broken")
        kept = []
        for row in self.details:
            if not row.get("FreeString13"):
                continue
            if not effective(row, self.ref_date):
                continue
            if row.get("FreeString14") not in yes_syscodes:
                continue
            kept.append(row)
        return kept

    # Step 4
    def group(self, details: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in details:
            groups[row["FreeString13"]].append(row)
        return groups

    # Step 5
    def anchor_of(self, key: str, members: list[dict]):
        """(anchor Property row, anchor PropertyDetails row)."""
        prop = self.property.get(key)
        if prop is None:
            self.note(f"facility key {key} does not resolve to a Property row")
            return None, None
        own = [m for m in members if m.get("PropertyRef") == key]
        if len(own) > 1:
            self.note(f"facility {prop.get('Code')} has {len(own)} anchor detail rows")
        if not own:
            self.note(
                f"facility {prop.get('Code')} has no member pointing at itself; "
                f"category and owner will be missing"
            )
            return prop, None
        return prop, own[0]

    # Step 6
    def assignable(self) -> dict[str, float]:
        """PropertyRef -> assignable square feet, nonassignable space excluded."""
        totals: dict[str, float] = defaultdict(float)
        for row in self.usage:
            if not effective(row, self.ref_date):
                continue
            standard = self.standard.get(row.get("SpaceStandardRef") or "")
            if standard is None:
                if row.get("SpaceStandardRef"):
                    self.note(
                        f"SpaceStandardRef {row['SpaceStandardRef']} "
                        f"not found in SpaceStandard"
                    )
                continue
            if standard.get("Code") == "000":
                continue
            # Both the space standard and its parent must be present and not
            # '000'. A missing parent excludes the record, per the migration
            # requirements. No numeric effect on current data (every standard in
            # use has a parent) but it is the agreed rule.
            parent = self.standard.get(standard.get("ParentRef") or "")
            if parent is None or parent.get("Code") == "000":
                continue
            totals[row.get("PropertyRef") or ""] += num(row.get("FloorArea"))
        return totals

    # Steps 7 to 9
    def build(self) -> list[dict]:
        self.load()
        details = self.included_details()
        groups = self.group(details)
        asf_by_property = self.assignable()

        rows = []
        for key, members in groups.items():
            anchor, anchor_detail = self.anchor_of(key, members)
            if anchor is None:
                continue

            # No second inclusion filter. Planon gated this report on a tag it
            # calls the FR Benchmark, but that was a workaround for the Data
            # Aggregation module being unable to select its own population. The
            # tag drifted out of sync with the real rule and silently dropped
            # buildings. "Reported to Chancellors Office" is the actual rule and
            # is the only thing that decides membership here.
            if self.drop_archived and (anchor.get("IsArchived") or "").lower() == "true":
                continue

            code = anchor.get("Code") or ""
            fac_num, sfx = split_code(code)

            cat_code, cat_desc = self.resolve(
                (anchor_detail or {}).get("FreeString8"), "PROPERTY_CSUUSETYPE")
            sta_code, sta_desc = self.resolve(
                anchor.get("FreeString5"), "PROPERTY_MPSTATUS")
            own_code, own_desc = self.resolve(
                (anchor_detail or {}).get("FreeString9"), "PROPERTY_OWNERSHIP")
            cen_code, cen_desc = self.resolve(
                anchor.get("FreeString7"), "PROPERTY_CENTER")

            gsf = sum(num(m.get("GrossFloorArea")) for m in members)
            asf = sum(asf_by_property.get(m.get("PropertyRef") or "", 0.0)
                      for m in members)

            rows.append({
                "FAC NUM": fac_num,
                "SFX": sfx,
                "FAC NAME": anchor.get("FreeString11") or "",
                "CATEGORY CODE": cat_code, "CATEGORY DESC": cat_desc,
                "STATUS CODE": sta_code, "STATUS DESC": sta_desc,
                "OWNER CODE": own_code, "OWNER DESC": own_desc,
                # A zero measure prints blank, not 0. Planon does this for both
                # GSF and ASF; Solano facilities 034 and 035 are the GSF cases.
                "GSF": round(gsf) if gsf else None,
                # ASF of zero means no assignable space was found, which the
                # report prints as blank rather than 0.
                "ASF": round(asf) if asf else None,
                "EFFC": round(asf / gsf * 100, 2) if (asf and gsf) else None,
                "COMPL DATE": (anchor.get("PurchaseDate") or "")[:10],
                "CENTER CODE": cen_code, "CENTER DESC": cen_desc,
                "_code": code,
            })

        # Step 8. No center filter. The requirements are explicit that a blank
        # CSU center must not exclude a property, and a whitelist of known center
        # codes does exactly that. Center is descriptive here: it labels and
        # groups the output, it never decides membership.
        for row in rows:
            if not row["FAC NUM"].isdigit() or len(row["FAC NUM"]) != 3:
                self.note(f"anchor code {row['_code']!r} is not a 3-digit facility number")
        # Step 9
        rows.sort(key=lambda r: (r["CENTER CODE"], r["_code"]))
        for row in rows:
            row.pop("_code")
        return rows


# ---------------------------------------------------------------------- output

def fmt_effc(gsf, asf) -> str:
    """Area 15 divided by Area 14, blank if either is blank or zero.

    This follows the migration requirements rather than Planon, which prints a
    literal "0" for the roughly 70 facilities that have gross area but no
    assignable area. A blank is the agreed behaviour and reads correctly as
    "not calculable" instead of "zero percent efficient".
    """
    if not gsf or not asf:
        return ""
    return repr(round(asf / gsf, 9))


def fmt_compl(date: str) -> str:
    """1964-11-01 -> 11-1964."""
    return f"{date[5:7]}-{date[0:4]}" if len(date) >= 7 else ""


def write_csv(rows: list[dict], path: str, ref_date: str) -> None:
    """Write the report in Planon's own export layout.

    One section per CSU center, introduced by a "<date> : <code> - <name>" row
    with the center columns folded into it, each section closed by a subtotal
    carrying only Area 14 and Area 15, and a grand total on the final line.
    Trailing comma on every row and CRLF endings, both as Planon emits them.
    """
    blank = [""] * len(COLUMNS)

    def line(fields):
        out = io.StringIO()
        csv.writer(out, lineterminator="").writerow(fields + [""])
        return out.getvalue() + "\r\n"

    def subtotal(group):
        _, gsf, asf = totals(group)
        row = list(blank)
        row[9], row[10] = gsf, asf or ""
        return line(row)

    sections: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        sections[(row["CENTER CODE"], row["CENTER DESC"])].append(row)

    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(line(COLUMNS))
        for (code, desc), group in sorted(sections.items()):
            handle.write(line([f"{ref_date} : {code} - {desc}"] + blank[1:]))
            for row in group:
                handle.write(line([
                    row["FAC NUM"], row["SFX"], row["FAC NAME"],
                    row["CATEGORY CODE"], row["CATEGORY DESC"],
                    row["STATUS CODE"], row["STATUS DESC"],
                    row["OWNER CODE"], row["OWNER DESC"],
                    row["GSF"] if row["GSF"] is not None else "",
                    row["ASF"] if row["ASF"] is not None else "",
                    fmt_effc(row["GSF"], row["ASF"]),
                    fmt_compl(row["COMPL DATE"]),
                ]))
            handle.write(subtotal(group))
        handle.write(subtotal(rows))


def totals(rows: list[dict]) -> tuple[int, int, int]:
    return (
        len(rows),
        sum(r["GSF"] or 0 for r in rows),
        sum(r["ASF"] or 0 for r in rows),
    )


def print_summary(rows: list[dict]) -> None:
    by_center: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_center[(row["CENTER CODE"], row["CENTER DESC"])].append(row)
    print(f"\n  {'center':<32} {'count':>6} {'GSF':>14} {'ASF':>14}")
    for (code, desc), group in sorted(by_center.items()):
        count, gsf, asf = totals(group)
        print(f"  {code + ' ' + desc:<32} {count:>6} {gsf:>14,} {asf:>14,}")
    count, gsf, asf = totals(rows)
    print(f"  {'GRAND TOTAL':<32} {count:>6} {gsf:>14,} {asf:>14,}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.path.join(ROOT, "source_data"))
    parser.add_argument("--ref-date", required=True,
                        help="YYYY-MM-DD. The date the report is a snapshot of. "
                             "Required: it selects which version of every "
                             "time-dependent record is in force.")
    parser.add_argument("--centers", default=",".join(CSU_CENTERS),
                        help="expected centers, for reporting only; not a filter")
    # Always lands at the repo root next to planon-reference.csv, so the
    # two are easy to compare and there is only ever one generated file.
    parser.add_argument("--out",
                        default=os.path.join(ROOT,
                                             "report.csv"))
    parser.add_argument("--drop-archived", action="store_true",
                        help="also drop facilities whose anchor is archived")
    args = parser.parse_args(argv)

    source = LocalSource(args.source)
    centers = [c.strip() for c in args.centers.split(",") if c.strip()]

    report = Report(source, args.ref_date, centers, args.drop_archived)
    rows = report.build()
    write_csv(rows, args.out, args.ref_date)

    print(f"  ref date : {args.ref_date}")
    print(f"  centers  : no filter (all centers reported, blanks included)")
    print(f"  wrote    : {args.out}  ({len(rows)} rows)")
    print_summary(rows)

    if report.problems:
        counts: dict[str, int] = defaultdict(int)
        for problem in report.problems:
            counts[problem] += 1
        print(f"\n  {len(report.problems)} relationship problems "
              f"({len(counts)} distinct):")
        for problem, n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
            print(f"    x{n:<5} {problem}")
    else:
        print("\n  every documented relationship held: no unresolved references, "
              "one anchor per facility, all codes in their expected groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
