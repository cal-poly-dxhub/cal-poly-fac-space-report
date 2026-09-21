"""build Lambda: turn the current copy of the tables into report.csv (diagram steps 5-6).

Bundled next to pipeline/build_report.py, which holds all the report logic. Called
by API Gateway as a proxy integration, so it takes and returns API Gateway's shapes.
"""

import datetime
import os

import boto3
from botocore.exceptions import ClientError

import build_report

SOURCE_DIR = "/tmp/source_data"
REPORT_PATH = "/tmp/report.csv"

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


def handler(event, context):
    raw = (event.get("queryStringParameters") or {}).get("ref_date", "")
    try:
        ref_date = datetime.datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return respond(400, "ref_date is required, as YYYY-MM-DD")

    bucket = os.environ["DATA_BUCKET"]
    prefix = os.environ["TABLES_PREFIX"]
    try:
        for table in build_report.TABLES:
            # The layout build_report.LocalSource reads: <root>/<Table>/<Table>.csv
            os.makedirs(f"{SOURCE_DIR}/{table}", exist_ok=True)
            s3.download_file(
                bucket, f"{prefix}{table}.csv", f"{SOURCE_DIR}/{table}/{table}.csv"
            )
    except ClientError as error:
        if error.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return respond(409, "No Planon data yet. Run Refresh first.")
        raise

    build_report.main(
        ["--source", SOURCE_DIR, "--ref-date", ref_date, "--out", REPORT_PATH]
    )
    with open(REPORT_PATH, encoding="utf-8") as handle:
        return respond(
            200,
            handle.read(),
            "text/csv",
            {"Content-Disposition": f'attachment; filename="report-{ref_date}.csv"'},
        )
