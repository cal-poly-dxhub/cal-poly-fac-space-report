"""pull Lambda: copy the four Planon tables into the data bucket (diagram step 3).

Bundled next to connector/planon_odata.py, which does the OData work.
"""

import json
import os
from datetime import datetime, timezone

import boto3

from planon_odata import PlanonODataClient, render

TABLES = ["Property", "PropertyDetails", "SpaceUsage", "BaseCodes"]

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")


def write_marker(bucket, key, marker):
    marker["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(marker).encode("utf-8"),
        ContentType="application/json",
    )
    return marker


def handler(event, context):
    bucket = os.environ["DATA_BUCKET"]
    marker_key = os.environ["MARKER_KEY"]

    # Drop the old marker first, so a poll never takes the last pull for this one.
    s3.delete_object(Bucket=bucket, Key=marker_key)
    try:
        row_counts = pull_tables(bucket, os.environ["TABLES_PREFIX"])
    except Exception as error:
        # Without this the page cannot tell a dead pull from a slow one. Re-raised
        # so Lambda still counts and logs the failure.
        write_marker(bucket, marker_key, {"status": "failed", "error": str(error)[:500]})
        raise
    return write_marker(bucket, marker_key, {"status": "complete", "rows": row_counts})


def pull_tables(bucket, prefix):
    secret = secrets.get_secret_value(SecretId=os.environ["PLANON_SECRET_ARN"])
    login = json.loads(secret["SecretString"])
    client = PlanonODataClient(username=login["username"], password=login["password"])

    row_counts = {}
    for table in TABLES:
        rows = list(client.query(table))
        s3.put_object(
            Bucket=bucket,
            Key=f"{prefix}{table}.csv",
            Body=render(rows, "csv").encode("utf-8"),
        )
        row_counts[table] = len(rows)
        print(f"wrote {len(rows)} rows of {table}")
    return row_counts
