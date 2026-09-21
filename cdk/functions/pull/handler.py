"""pull Lambda: copy the five Planon tables into the data bucket (diagram step 3).

Bundled next to connector/planon_odata.py, which does the OData work.
"""

import json
import os
from datetime import datetime, timezone

import boto3

from planon_odata import PlanonODataClient, render

TABLES = ["Property", "PropertyDetails", "SpaceUsage", "SpaceStandard", "BaseCodes"]

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")


def handler(event, context):
    bucket = os.environ["DATA_BUCKET"]
    prefix = os.environ["TABLES_PREFIX"]
    marker_key = os.environ["MARKER_KEY"]

    # Drop the old marker first, so a poll never takes the last pull for this one.
    s3.delete_object(Bucket=bucket, Key=marker_key)

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

    marker = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": row_counts,
    }
    s3.put_object(
        Bucket=bucket,
        Key=marker_key,
        Body=json.dumps(marker).encode("utf-8"),
        ContentType="application/json",
    )
    return marker
