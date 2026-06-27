import json
import os

import boto3
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
BUCKET = os.environ["SALONS_BUCKET_NAME"]
KEY = os.environ.get("SALONS_OBJECT_KEY", "salons.json")


def handler(event, context):
    try:
        response = s3.get_object(Bucket=BUCKET, Key=KEY)
        body = response["Body"].read().decode("utf-8")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return _response(404, {"error": "salons.json not found yet - has the generator Lambda run?"})
        return _response(500, {"error": str(exc)})

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": body,
    }


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload),
    }
