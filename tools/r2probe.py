"""R2 connectivity probe — runs inside ratis-product_analyser container.

Usage on prod VM:
    docker cp tools/r2probe.py ratis-product_analyser-1:/tmp/
    docker compose -f docker-compose.prod.yml exec -T product_analyser python /tmp/r2probe.py
"""

import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def main() -> None:
    endpoint = os.environ.get("R2_ENDPOINT_URL", "")
    bucket = os.environ.get("R2_BUCKET_NAME", "")
    akid = os.environ.get("R2_ACCESS_KEY_ID", "")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "")

    print(f"Endpoint: {endpoint!r}")
    print(f"Bucket:   {bucket!r}")
    print(f"AKID:     {akid[:8]}... (len={len(akid)})")
    print(f"Secret:   ***{secret[-4:] if len(secret) >= 4 else '?'} (len={len(secret)})")

    if not all([endpoint, bucket, akid, secret]):
        print("ABORT: one or more env vars missing")
        return

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=akid,
        aws_secret_access_key=secret,
    )

    print()
    print("--- head_bucket ---")
    try:
        client.head_bucket(Bucket=bucket)
        print("OK")
    except (ClientError, BotoCoreError) as e:
        print(f"FAIL {type(e).__name__}: {e}")

    print()
    print("--- put_object ---")
    try:
        r = client.put_object(Bucket=bucket, Key="_diag_probe.txt", Body=b"hello")
        print(f"OK status={r.get('ResponseMetadata', {}).get('HTTPStatusCode')}")
        client.delete_object(Bucket=bucket, Key="_diag_probe.txt")
        print("delete OK")
    except (ClientError, BotoCoreError) as e:
        print(f"FAIL {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
