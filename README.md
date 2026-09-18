# fac-space-report

Rebuild of Cal Poly's annual CSU facility report, replacing Planon's Data
Aggregation Manager before it retires in December 2026.

## What's here

| | |
| --- | --- |
| `report-column-paths.md` | Where each report column comes from |
| `docs/aws-deployment.drawio` | Proposed AWS deployment diagram. Design only, nothing is deployed yet. |
| `pipeline/` | The pipeline, plus a script that diffs our output against Planon's |
| `connector/` | OData client for pulling Planon tables |
| `source_data/` | (When pulled) Local mirror of the five source tables. Create locally using script below. |
| `planon-reference.csv` | (When added) Planon's own output. Not in the repo, add manually in order to run compare.py. |

## Getting set up

No Cal Poly facility data is committed. The repo holds code and
documentation only; below is instructions to get started.

You need Planon datalake credentials.

    cd connector
    cp .env.example .env        # fill in PLANON_USERNAME and PLANON_PASSWORD
    for t in Property PropertyDetails SpaceUsage SpaceStandard BaseCodes; do
      python3 planon_odata.py $t --format csv --out ../source_data/$t/$t.csv
    done

That writes the five source tables into `source_data/`. Re-run it whenever you
need current data.

## Generate the report

    python3 pipeline/build_report.py --ref-date 2026-09-10

Writes `report.csv`. The reference date is required. If you want current, put today's date.

Check it against Planon's own output:

    python3 pipeline/compare.py

Needs `planon-reference.csv` at the root, exported from Planon by whoever runs
the report there.

## How it works

Five OData tables, no Planon-side logic carried over.

    Property  PropertyDetails  SpaceUsage  SpaceStandard  BaseCodes

A report row is a **facility**, which is a group of buildings sharing
`PropertyDetails.FreeString13`. That field points at one member, the **anchor**,
and every descriptive value comes from it. Areas sum across the whole group.

Two filters, both from the requirements doc: the reference date, and
`PropertyDetails.FreeString14` ("Reported to Chancellors Office") = Y.

See `report-column-paths.md` for where each column comes from.

## Note: Difference from Planon

EFFC. Where a facility has gross area but no assignable area, Planon prints `0`
and we print blank. This is the only place the two disagree, and it follows the requirements doc. 