# Running it locally

The same report the deployed page makes, built on your own machine with no AWS. Useful for
working on the report logic, and for checking our output against Planon's.

Needs Python 3.10+ and Planon datalake credentials. `build_report.py` and `compare.py` use the
standard library only; the connector needs `requests`.

## Pull the data

```bash
cd connector
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env        # fill in PLANON_USERNAME and PLANON_PASSWORD

for t in Property PropertyDetails SpaceUsage SpaceStandard BaseCodes; do
  mkdir -p ../source_data/$t
  python3 planon_odata.py $t --format csv --out ../source_data/$t/$t.csv
done
cd ..
```

Run it from `connector/`, which is where it looks for `.env`. It writes the five tables into
`source_data/`, which is gitignored. Re-run it whenever you need current data. More on the
connector: [`connector/README.md`](../connector/README.md).

## Generate the report

```bash
python3 pipeline/build_report.py --ref-date 2026-09-10
```

Writes `report.csv` at the repo root. The reference date is required: it picks which version
of every dated record is in force. For current data, use today's date.

## Check it against Planon

```bash
python3 pipeline/compare.py
```

Needs `planon-reference.csv` at the repo root, exported from Planon by whoever runs the report
there. It is gitignored like all facility data. The script reads the reference date out of
that export and builds its own report from `source_data/` for that date, so it doesn't need
`report.csv` and the dates can't drift apart. On current data every column matches on all 187
facilities, with two expected exceptions:

- **FAC NAME, 2 rows.** Two names use `ʔ` and `ʸ`. Planon's export is Windows-1252, which
  can't hold those, so it prints `?`. Ours keeps the real characters.
- **"in planon only", 6 keys with blank codes.** Those are the six center subtotal rows in
  Planon's file, not facilities.

EFFC isn't compared. It's the one real rule difference: where a facility has gross area but
no assignable area, Planon prints `0` and we print blank, which is what the requirements doc
asks for.

Where each column comes from: [`docs/report-column-paths.md`](../docs/report-column-paths.md).
