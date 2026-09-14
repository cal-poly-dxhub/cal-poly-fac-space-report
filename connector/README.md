# PlanOn Connector

Pulls data from the Planon datalake OData service at
`https://planon.calpoly.edu/datalake/odata`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in PLANON_USERNAME / PLANON_PASSWORD
```

## The 10-row Property smoke test

```bash
python planon_odata.py Property \
  --select Syscode,FreeString7,Name,PurchaseDate,Code \
  --top 10
```

## Other examples

```bash
# What tables does the service actually expose?
python planon_odata.py --list-tables

# Whole Space table to newline-delimited JSON (auto-pages via @odata.nextLink)
python planon_odata.py Space --out space.ndjson --format ndjson

# CSV straight into S3
python planon_odata.py Property --top 10 --format csv \
  --s3-uri s3://my-bucket/planon/property.csv

# Server-side filtering
python planon_odata.py Space --filter "PropertyRef eq 'ABC'" --top 100
```

## Table name mapping

| OData entity set | Planon SQL table |
| --- | --- |
| `Property` | `OBJALG` |
| `PropertyDetails` | `PLN_PROPERTY_DETAILS` |
| `Space` | `RMT` |
| `SpaceUsage` | `RMTGBR` |
| `SpaceStandard` | `PLN_SPACESTANDARD` |

## Notes

- `--top` caps total rows across all pages; paging is handled for you, so
  omitting it pulls the full table.
- OData omits fields that are null, so the CSV writer takes the union of keys
  across the fetched rows to build its header.
- If TLS verification fails against the Cal Poly cert chain, `--insecure` will
  get you unblocked for debugging — don't leave it on for anything scheduled.
