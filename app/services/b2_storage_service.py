"""Backblaze B2 S3-compatible direct upload and worker download service."""

import asyncio
from functools import lru_cache
from urllib.parse import urlparse

import boto3
import httpx
from botocore.client import Config

from app.core.config import Settings


class B2ConfigurationError(RuntimeError):
    pass


def _configuration() -> Settings:
    # Storage credentials are intentionally refreshed from the environment file
    # for each job boundary so rotating a B2 key does not require an API restart.
    config = Settings()
    missing = [
        name
        for name, value in {
            "B2_KEY_ID": config.B2_KEY_ID,
            "B2_APPLICATION_KEY": config.B2_APPLICATION_KEY,
            "B2_BUCKET": config.B2_BUCKET,
        }.items()
        if not value
    ]
    if missing:
        raise B2ConfigurationError(f"Missing Backblaze configuration: {', '.join(missing)}")
    return config


async def _discover_endpoint() -> str:
    config = _configuration()
    if config.B2_S3_ENDPOINT:
        return config.B2_S3_ENDPOINT.rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            "https://api.backblazeb2.com/b2api/v4/b2_authorize_account",
            auth=(config.B2_KEY_ID, config.B2_APPLICATION_KEY),
        )
    response.raise_for_status()
    return response.json()["apiInfo"]["storageApi"]["s3ApiUrl"].rstrip("/")


def _region_from_endpoint(endpoint: str) -> str:
    host = urlparse(endpoint).hostname or ""
    if host.startswith("s3.") and host.endswith(".backblazeb2.com"):
        return host.removeprefix("s3.").removesuffix(".backblazeb2.com")
    raise B2ConfigurationError("Could not determine Backblaze region from S3 endpoint")


@lru_cache(maxsize=4)
def _client(endpoint: str, key_id: str, application_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=_region_from_endpoint(endpoint),
        aws_access_key_id=key_id,
        aws_secret_access_key=application_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


async def create_upload_url(*, object_key: str, content_type: str) -> str:
    config = _configuration()
    endpoint = await _discover_endpoint()
    client = _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY)
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "put_object",
        Params={
            "Bucket": config.B2_BUCKET,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=config.B2_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="PUT",
    )


async def create_download_url(*, object_key: str) -> str:
    """Create a short-lived private-object URL only when a clinician requests it."""
    config = _configuration()
    endpoint = await _discover_endpoint()
    client = _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY)
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": config.B2_BUCKET, "Key": object_key},
        ExpiresIn=config.B2_PRESIGN_EXPIRE_SECONDS,
        HttpMethod="GET",
    )


async def verify_upload(*, object_key: str, expected_size: int) -> dict:
    config = _configuration()
    endpoint = await _discover_endpoint()
    response = await asyncio.to_thread(
        _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY).head_object,
        Bucket=config.B2_BUCKET,
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
    endpoint = await _discover_endpoint()
    response = await asyncio.to_thread(
        _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY).get_object,
        Bucket=config.B2_BUCKET,
        Key=object_key,
    )
    return await asyncio.to_thread(response["Body"].read)


async def download_object(*, object_key: str) -> bytes:
    return await download_audio(object_key=object_key)


async def upload_object(
    *, object_key: str, content_type: str, data: bytes
) -> dict:
    config = _configuration()
    endpoint = await _discover_endpoint()
    return await asyncio.to_thread(
        _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY).put_object,
        Bucket=config.B2_BUCKET,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


async def configure_browser_cors(origins: list[str]) -> None:
    config = _configuration()
    endpoint = await _discover_endpoint()
    await asyncio.to_thread(
        _client(endpoint, config.B2_KEY_ID, config.B2_APPLICATION_KEY).put_bucket_cors,
        Bucket=config.B2_BUCKET,
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
