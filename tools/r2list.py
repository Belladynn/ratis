"""List R2 bucket contents — runs inside ratis-product_analyser container."""

import os

import boto3

client = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT_URL"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
)
r = client.list_objects_v2(Bucket=os.environ["R2_BUCKET_NAME"], MaxKeys=20)
contents = r.get("Contents", [])
print(f"Bucket has {len(contents)} objects (showing up to 20):")
for o in contents:
    print(f"  {o['Key']}  size={o['Size']}  modified={o['LastModified']}")
