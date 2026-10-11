"""Runtime configuration shared by the API: secrets and the access mode."""
import logging
import os
import time

try:
    import boto3
except ImportError:
    boto3 = None

logger = logging.getLogger("heatshield.config")

_PARAMETER_TTL_SECONDS = 300
_parameter_cache: dict[str, tuple[str | None, float]] = {}


def secret(env_name: str, parameter_env_name: str | None = None) -> str | None:
    """Reads a secret from the environment, else from the SSM parameter named by `parameter_env_name`."""
    value = os.getenv(env_name)
    if value:
        return value
    parameter_name = os.getenv(parameter_env_name) if parameter_env_name else None
    if not boto3 or not parameter_name:
        return None
    cached = _parameter_cache.get(parameter_name)
    if cached and cached[1] > time.time():
        return cached[0]
    value = None
    try:
        value = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=True)["Parameter"]["Value"]
    except Exception as e:  # noqa: BLE001
        logger.warning("SSM get_parameter failed for %s: %s", parameter_name, type(e).__name__)
    # Misses are cached too, so an unset optional parameter costs one SSM call per TTL, not one per request.
    _parameter_cache[parameter_name] = (value, time.time() + _PARAMETER_TTL_SECONDS)
    return value


def open_access() -> bool:
    """True only when the operator has explicitly opted out of authentication (local dev, demos)."""
    return os.getenv("HEATSHIELD_OPEN_ACCESS", "false").lower() == "true"
