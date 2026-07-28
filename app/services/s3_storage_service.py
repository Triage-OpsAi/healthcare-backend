"""AWS S3 direct upload, signed download, and worker storage service."""

import asyncio
from functools import lru_cache

import boto3
from botocore.client import Config

from app.core.config import Settings


class S3ConfigurationError(RuntimeError):
    pass


def _configuration() -> Settings:
    # Refresh credentials from .env at each job boundary so key rotation does not
    # require restarting the API or Celery workers.
    config = Settings()
    missing = [
        name
        for name, value in {
            "AWS_ACCESS_KEY_ID": config.AWS_ACCESS_KEY_ID,
            "AWS_SECRET_ACCESS_KEY": config.AWS_SECRET_ACCESS_KEY,
            "AWS_S3_BUCKET": config.AWS_S3_BUCKET,
            "AWS_REGION": config.AWS_REGION,
        }.items()
        if not value
    ]
    if missing:
        raise S3ConfigurationError(f"Missing AWS S3 configuration: {', '.join(missing)}")
    return config


@lru_cache(maxsize=4)
def _client(region: str, access_key_id: str, secret_access_key: str):
    return boto3.client(
        "s3",
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


def _configured_client(config: Settings):
    return _client(
        config.AWS_REGION,
        config.AWS_ACCESS_KEY_ID,
        config.AWS_SECRET_ACCESS_KEY,
    )


async def create_upload_url(*, object_key: str, content_type: str) -> str:
    config = _configuration()
    client = _configured_client(config)
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "put_object",
        Params={
            "Bucket": config.AWS_S3_BUCKET,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=config.AWS_S3_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="PUT",
    )


async def create_download_url(*, object_key: str) -> str:
    """Create a short-lived private-object URL only when a clinician requests it."""
    config = _configuration()
    client = _configured_client(config)
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": config.AWS_S3_BUCKET, "Key": object_key},
        ExpiresIn=config.AWS_S3_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="GET",
    )


async def verify_upload(*, object_key: str, expected_size: int) -> dict:
    config = _configuration()
    response = await asyncio.to_thread(
        _configured_client(config).head_object,
        Bucket=config.AWS_S3_BUCKET,
        Key=object_key,
    )
    actual_size = int(response["ContentLength"])
    if actual_size != expected_size:
        raise ValueError(
            f"Uploaded object size mismatch: expected {expected_size}, received {actual_size}"
        )
    return response


async def download_audio(*, object_key: str) -> bytes:
    config = _configuration()
    response = await asyncio.to_thread(
        _configured_client(config).get_object,
        Bucket=config.AWS_S3_BUCKET,
        Key=object_key,
    )
    return await asyncio.to_thread(response["Body"].read)


async def download_object(*, object_key: str) -> bytes:
    return await download_audio(object_key=object_key)


async def upload_object(
    *, object_key: str, content_type: str, data: bytes
) -> dict:
    config = _configuration()
    return await asyncio.to_thread(
        _configured_client(config).put_object,
        Bucket=config.AWS_S3_BUCKET,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


async def configure_browser_cors(origins: list[str]) -> None:
    config = _configuration()
    await asyncio.to_thread(
        _configured_client(config).put_bucket_cors,
        Bucket=config.AWS_S3_BUCKET,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedHeaders": ["content-type", "range", "x-amz-*"],
                    "AllowedMethods": ["GET", "PUT", "HEAD"],
                    "AllowedOrigins": origins,
                    "ExposeHeaders": [
                        "ETag",
                        "Accept-Ranges",
                        "Content-Length",
                        "Content-Range",
                    ],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )
