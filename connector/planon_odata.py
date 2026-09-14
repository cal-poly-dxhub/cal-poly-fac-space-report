"""Pull data from the Planon datalake OData endpoint.

Reads credentials from environment variables (see .env.example) and writes
results to stdout, a local file, or S3.

Examples
--------
    # The 10-row Property smoke test
    python planon_odata.py Property \
        --select Syscode,FreeString7,Name,PurchaseDate,Code --top 10

    # Full table, paged, to newline-delimited JSON
    python planon_odata.py Space --out space.ndjson --format ndjson

    # Straight to S3 as CSV
    python planon_odata.py Property --top 10 --format csv \
        --s3-uri s3://my-bucket/planon/property.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from typing import Any, Iterator

import requests

DEFAULT_BASE_URL = "https://planon.calpoly.edu/datalake/odata"
PAGE_SIZE = 1000
TIMEOUT = 60


class PlanonODataError(RuntimeError):
    pass


class PlanonODataClient:
    """Minimal OData v4 client for the Planon datalake service."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        verify: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = verify
        self.session.headers.update({"Accept": "application/json"})

        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif username and password:
            self.session.auth = (username, password)
        else:
            raise PlanonODataError(
                "No credentials found. Set PLANON_USERNAME/PLANON_PASSWORD "
                "or PLANON_TOKEN (see .env.example)."
            )

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=TIMEOUT)
        if response.status_code == 401:
            raise PlanonODataError(
                f"401 Unauthorized from {url} — check the credentials and that "
                "the account has datalake OData access."
            )
        if not response.ok:
            # OData errors carry a useful JSON body; fall back to raw text.
            detail = response.text[:1000]
            raise PlanonODataError(
                f"HTTP {response.status_code} from {url}\n{detail}"
            )
        return response.json()

    def entity_sets(self) -> list[str]:
        """List the entity sets (OData 'table' names) the service exposes."""
        doc = self._get(self.base_url)
        return sorted(item["name"] for item in doc.get("value", []))

    def query(
        self,
        entity_set: str,
        select: str | None = None,
        filter_: str | None = None,
        orderby: str | None = None,
        top: int | None = None,
        page_size: int = PAGE_SIZE,
    ) -> Iterator[dict[str, Any]]:
        """Yield rows from ``entity_set``, following server-side paging.

        ``top`` caps the total number of rows returned across all pages.
        """
        params: dict[str, Any] = {}
        if select:
            params["$select"] = select
        if filter_:
            params["$filter"] = filter_
        if orderby:
            params["$orderby"] = orderby
        # NOTE: do not send $top. This service treats it as a hard total cap and
        # then omits @odata.nextLink, so any $top silently truncates the pull to
        # one page. Page server-side and enforce ``top`` client-side instead.

        url = f"{self.base_url}/{entity_set}"
        emitted = 0

        while url:
            payload = self._get(url, params=params)
            for row in payload.get("value", []):
                yield row
                emitted += 1
                if top and emitted >= top:
                    return

            # nextLink is a fully-formed URL with its own query string.
            url = payload.get("@odata.nextLink")
            params = None


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file without adding a dependency."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def render(rows: list[dict[str, Any]], fmt: str) -> str:
    """Serialize rows as pretty JSON, newline-delimited JSON, or CSV."""
    if fmt == "json":
        return json.dumps(rows, indent=2, default=str)
    if fmt == "ndjson":
        return "\n".join(json.dumps(row, default=str) for row in rows)
    if fmt == "csv":
        if not rows:
            return ""
        # Union of keys, first-seen order, since OData omits null fields.
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns and not key.startswith("@odata"):
                    columns.append(key)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()
    raise ValueError(f"Unsupported format: {fmt}")


def put_s3(uri: str, body: str) -> None:
    import boto3  # imported lazily so boto3 stays optional

    bucket, _, key = uri.removeprefix("s3://").partition("/")
    if not bucket or not key:
        raise PlanonODataError(f"Malformed S3 URI: {uri} (expected s3://bucket/key)")
    boto3.client("s3").put_object(
        Bucket=bucket, Key=key, Body=body.encode("utf-8")
    )
    print(f"Wrote {len(body)} bytes to {uri}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull rows from a Planon datalake OData entity set."
    )
    parser.add_argument(
        "entity_set",
        nargs="?",
        help="OData table name, e.g. Property, PropertyDetails, Space, "
        "SpaceUsage, SpaceStandard",
    )
    parser.add_argument(
        "--list-tables",
        action="store_true",
        help="List available entity sets and exit",
    )
    parser.add_argument("--select", help="$select — comma-separated field list")
    parser.add_argument("--filter", dest="filter_", help="$filter expression")
    parser.add_argument("--orderby", help="$orderby expression")
    parser.add_argument(
        "--top", type=int, help="Max total rows to fetch (omit for the whole table)"
    )
    parser.add_argument(
        "--page-size", type=int, default=PAGE_SIZE, help="Rows per request"
    )
    parser.add_argument(
        "--format",
        choices=["json", "ndjson", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument("--out", help="Write to this local file instead of stdout")
    parser.add_argument("--s3-uri", help="Also upload the output to s3://bucket/key")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PLANON_BASE_URL", DEFAULT_BASE_URL),
        help=f"OData service root (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS verification (only for debugging cert issues)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    if not args.entity_set and not args.list_tables:
        build_parser().error("give an entity_set or use --list-tables")

    try:
        client = PlanonODataClient(
            base_url=args.base_url,
            username=os.environ.get("PLANON_USERNAME"),
            password=os.environ.get("PLANON_PASSWORD"),
            token=os.environ.get("PLANON_TOKEN"),
            verify=not args.insecure,
        )

        if args.list_tables:
            for name in client.entity_sets():
                print(name)
            return 0

        rows = list(
            client.query(
                args.entity_set,
                select=args.select,
                filter_=args.filter_,
                orderby=args.orderby,
                top=args.top,
                page_size=args.page_size,
            )
        )
    except PlanonODataError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Fetched {len(rows)} row(s) from {args.entity_set}", file=sys.stderr)
    output = render(rows, args.format)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as handle:
            handle.write(output)
        print(f"Wrote {args.out}", file=sys.stderr)
    if args.s3_uri:
        put_s3(args.s3_uri, output)
    if not args.out and not args.s3_uri:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
