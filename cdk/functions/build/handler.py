"""build Lambda: turn the current copy of the tables into report.csv (diagram steps 5-6).

Bundled next to pipeline/build_report.py, which holds all the report logic. Called
by API Gateway as a proxy integration, so it takes and returns API Gateway's shapes.
"""

import datetime
import json
import os

import boto3
from botocore.exceptions import ClientError

import build_report

SOURCE_DIR = "/tmp/source_data"
REPORT_PATH = "/tmp/report.csv"
NOT_READY = "No finished refresh on record. Run Refresh, or wait for the one running to finish."

s3 = boto3.client("s3")


def respond(status, body, content_type="text/plain", headers=None):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type,
            # The page is served from CloudFront, a different origin from the API.
            "Access-Control-Allow-Origin": os.environ["SITE_ORIGIN"],
            "Access-Control-Expose-Headers": "Content-Disposition",
            **(headers or {}),
        },
        "body": body,
    }


def complete_marker(bucket):
    """The marker's ETag if the last refresh finished cleanly, else None."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=os.environ["MARKER_KEY"])
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return None  # never refreshed, or a refresh is running
        raise
    if json.loads(obj["Body"].read()).get("status") != "complete":
        return None
    return obj["ETag"]


def handler(event, context):
    raw = (event.get("queryStringParameters") or {}).get("ref_date", "")
    try:
        ref_date = datetime.datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return respond(400, "ref_date is required, as YYYY-MM-DD")

    bucket = os.environ["DATA_BUCKET"]
    prefix = os.environ["TABLES_PREFIX"]
    # The pull overwrites the tables one at a time, so they only belong together
    # while a complete marker stands, and only if it is the same one after the
    # download. A pull deletes the marker before it writes any table.
    marker = complete_marker(bucket)
    if not marker:
        return respond(409, NOT_READY)
    for table in build_report.TABLES:
        # The layout build_report.LocalSource reads: <root>/<Table>/<Table>.csv
        os.makedirs(f"{SOURCE_DIR}/{table}", exist_ok=True)
        s3.download_file(
            bucket, f"{prefix}{table}.csv", f"{SOURCE_DIR}/{table}/{table}.csv"
        )
    if complete_marker(bucket) != marker:
        return respond(409, NOT_READY)

    status = build_report.main(
        ["--source", SOURCE_DIR, "--ref-date", ref_date, "--out", REPORT_PATH]
    )
    if status != 0:
        raise RuntimeError(f"build_report exited with status {status}")
    with open(REPORT_PATH, encoding="utf-8") as handle:
        return respond(
            200,
            handle.read(),
            "text/csv",
            {"Content-Disposition": f'attachment; filename="report-{ref_date}.csv"'},
        )
