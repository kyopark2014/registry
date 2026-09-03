#!/usr/bin/env python3
"""
AWS Agent Registry provisioner using boto3.

Creates an Agent Registry for local / org discovery of MCP servers,
A2A agents, skills, and custom resources.

Requires boto3/botocore >= 1.43.84 for the GA agent-registry-control client.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import boto3

# Configuration
project_name = "registry-harness"  # at least 3 characters
region = "us-west-2"
MIN_BOTO3_VERSION = "1.43.84"


def _version_tuple(version: str) -> Tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple."""
    parts = []
    for token in version.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)

sts_client = boto3.client("sts", region_name=region)
account_id = sts_client.get_caller_identity()["Account"]

registry_name = project_name
registry_description = (
    f"Agent Registry for {project_name}: "
    "central catalog of MCP servers, A2A agents, skills, and custom resources"
)

# Prefer the GA agent-registry namespace; fall back to preview bedrock-agentcore.
CONTROL_SERVICE_CANDIDATES = (
    {
        "service_name": "agent-registry-control",
        "endpoint_url": f"https://agent-registry-control.{region}.api.aws",
    },
    {
        "service_name": "bedrock-agentcore-control",
        "endpoint_url": None,
    },
)

WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WORKING_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "application", "config.json")


def setup_logging(log_level=logging.INFO):
    """Setup logging configuration."""
    log_format = "%(asctime)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(),
        ],
    )

    return logging.getLogger(__name__)


logger = setup_logging()


def _check_boto3_version() -> None:
    """Warn when boto3 is older than the GA Agent Registry requirement."""
    installed = boto3.__version__
    if _version_tuple(installed) < _version_tuple(MIN_BOTO3_VERSION):
        logger.warning(
            "boto3 %s is below %s. "
            "Install with: pip install -r requirements.txt "
            "(agent-registry-control requires >= %s)",
            installed,
            MIN_BOTO3_VERSION,
            MIN_BOTO3_VERSION,
        )
    else:
        logger.info(f"boto3 {installed} (>= {MIN_BOTO3_VERSION})")


def _create_control_client() -> Tuple[object, str]:
    """Return a control-plane client for Agent Registry."""
    last_error = None
    for candidate in CONTROL_SERVICE_CANDIDATES:
        service_name = candidate["service_name"]
        endpoint_url = candidate["endpoint_url"]
        try:
            kwargs = {"service_name": service_name, "region_name": region}
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url
            client = boto3.client(**kwargs)
            # Validate the service model is available in this SDK build.
            _ = client.meta.service_model.operation_model("CreateRegistry")
            logger.info(f"Using control-plane service: {service_name}")
            if endpoint_url:
                logger.info(f"  Endpoint: {endpoint_url}")
            elif service_name == "bedrock-agentcore-control":
                logger.warning(
                    "  Falling back to preview namespace "
                    "(bedrock-agentcore-control). Upgrade boto3 >= %s "
                    "for GA agent-registry-control.",
                    MIN_BOTO3_VERSION,
                )
            return client, service_name
        except Exception as e:
            last_error = e
            continue
    tried = [c["service_name"] for c in CONTROL_SERVICE_CANDIDATES]
    raise RuntimeError(
        "No Agent Registry control client available. "
        f"Tried {tried}. Last error: {last_error}. "
        f"Upgrade boto3/botocore to >= {MIN_BOTO3_VERSION} "
        "for agent-registry-control."
    )


def _list_all_registries(control_client) -> List[Dict]:
    """List all registries in the account/region."""
    registries: List[Dict] = []
    next_token = None
    while True:
        kwargs = {}
        if next_token:
            kwargs["nextToken"] = next_token
        response = control_client.list_registries(**kwargs)
        registries.extend(response.get("registries", []))
        next_token = response.get("nextToken")
        if not next_token:
            break
    return registries


def wait_for_registry_ready(
    control_client,
    registry_id: str,
    timeout_seconds: int = 600,
) -> Dict:
    """Wait until a registry reaches READY status."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        registry = control_client.get_registry(registryId=registry_id)
        status = registry.get("status", "")
        if status == "READY":
            logger.info(f"  Registry is ready: {registry_id}")
            return registry
        if status in (
            "CREATE_FAILED",
            "UPDATE_FAILED",
            "DELETE_FAILED",
            "DELETING",
        ):
            reason = registry.get("statusReason", "")
            raise RuntimeError(
                f"Registry {registry_id} entered terminal status: {status}"
                + (f" ({reason})" if reason else "")
            )
        logger.info(f"  Waiting for registry ({registry_id}) status: {status}")
        time.sleep(10)
    raise TimeoutError(
        f"Timed out waiting for registry {registry_id} to become READY"
    )


def mcp_endpoint_url(registry_id: str, service_name: str) -> str:
    """Build the Registry MCP discovery endpoint URL."""
    if service_name.startswith("agent-registry"):
        return (
            f"https://agent-registry.{region}.api.aws/"
            f"registry/{registry_id}/mcp"
        )
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/"
        f"registry/{registry_id}/mcp"
    )


def _authorizer_type_from_registry(registry: Dict) -> str:
    """Read authorizer type from GA or preview registry payloads."""
    discovery = registry.get("discoveryConfiguration") or {}
    return (
        discovery.get("authorizerType")
        or registry.get("authorizerType")
        or "AWS_IAM"
    )


def _create_registry_kwargs(service_name: str) -> Dict:
    """Build CreateRegistry kwargs for GA vs preview API schemas."""
    kwargs: Dict = {
        "name": registry_name,
        "description": registry_description,
    }
    if service_name.startswith("agent-registry"):
        # GA schema (boto3 >= 1.43.84)
        kwargs["discoveryConfiguration"] = {"authorizerType": "AWS_IAM"}
        kwargs["approvalConfiguration"] = {"autoApprovalRules": ["APPROVE_ALL"]}
        kwargs["tags"] = {"project": project_name, project_name: "true"}
    else:
        # Preview schema (bedrock-agentcore-control)
        kwargs["authorizerType"] = "AWS_IAM"
        kwargs["approvalConfiguration"] = {"autoApproval": True}
    return kwargs


def create_registry(control_client, service_name: str) -> Dict[str, str]:
    """Create Agent Registry (idempotent by name)."""
    logger.info(f"[1/1] Creating Agent Registry: {registry_name}")

    for existing in _list_all_registries(control_client):
        if existing.get("name") == registry_name:
            registry_id = existing["registryId"]
            logger.warning(
                f"  Registry already exists: {registry_name} ({registry_id})"
            )
            registry = wait_for_registry_ready(control_client, registry_id)
            return {
                "registry_id": registry_id,
                "registry_arn": registry.get(
                    "registryArn", existing.get("registryArn", "")
                ),
                "registry_name": registry_name,
                "authorizer_type": _authorizer_type_from_registry(registry)
                or _authorizer_type_from_registry(existing),
                "status": registry.get("status", existing.get("status", "")),
                "mcp_url": mcp_endpoint_url(registry_id, service_name),
                "control_service": service_name,
                "control_endpoint": control_client.meta.endpoint_url,
            }

    response = control_client.create_registry(**_create_registry_kwargs(service_name))
    registry_arn = response["registryArn"]
    registry_id = registry_arn.split("/")[-1]
    logger.info(f"  ✓ Registry created: {registry_id}")
    logger.info(f"  ARN: {registry_arn}")

    registry = wait_for_registry_ready(control_client, registry_id)
    return {
        "registry_id": registry.get("registryId", registry_id),
        "registry_arn": registry.get("registryArn", registry_arn),
        "registry_name": registry_name,
        "authorizer_type": _authorizer_type_from_registry(registry),
        "status": registry.get("status", "READY"),
        "mcp_url": mcp_endpoint_url(registry_id, service_name),
        "control_service": service_name,
        "control_endpoint": control_client.meta.endpoint_url,
    }


def write_application_config(config_data: Dict, *, merge_existing: bool = True) -> bool:
    """Write registry settings to application/config.json."""
    existing = {}
    if merge_existing:
        try:
            with open(CONFIG_PATH, "r") as f:
                existing = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Could not read existing {CONFIG_PATH}: {e}")

    existing.update(config_data)
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(existing, f, indent=2)
        return True
    except Exception as e:
        logger.warning(f"Could not write {CONFIG_PATH}: {e}")
        return False


def build_config(registry_info: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build application config payload."""
    config_data: Dict[str, str] = {
        "projectName": project_name,
        "accountId": account_id,
        "region": region,
        "boto3_version": boto3.__version__,
    }
    if registry_info:
        config_data.update(
            {
                "registry_name": registry_info.get("registry_name", registry_name),
                "registry_id": registry_info.get("registry_id", ""),
                "registry_arn": registry_info.get("registry_arn", ""),
                "registry_authorizer_type": registry_info.get(
                    "authorizer_type", "AWS_IAM"
                ),
                "registry_status": registry_info.get("status", ""),
                "registry_mcp_url": registry_info.get("mcp_url", ""),
                "registry_control_service": registry_info.get(
                    "control_service", ""
                ),
                "registry_control_endpoint": registry_info.get(
                    "control_endpoint", ""
                ),
            }
        )
    return config_data


def main():
    logger.info("=" * 60)
    logger.info("Starting Agent Registry Deployment")
    logger.info("=" * 60)
    logger.info(f"Project: {project_name}")
    logger.info(f"Region: {region}")
    logger.info(f"Account ID: {account_id}")
    logger.info(f"Registry Name: {registry_name}")
    _check_boto3_version()
    logger.info("=" * 60)

    start_time = time.time()
    registry_info = None
    deployment_success = False

    try:
        control_client, service_name = _create_control_client()
        registry_info = create_registry(control_client, service_name)
        deployment_success = True

        elapsed_time = time.time() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info("Agent Registry Deployment Completed Successfully!")
        logger.info("=" * 60)
        logger.info(f"  Registry Name: {registry_info['registry_name']}")
        logger.info(f"  Registry ID: {registry_info['registry_id']}")
        logger.info(f"  Registry ARN: {registry_info['registry_arn']}")
        logger.info(f"  Authorizer: {registry_info['authorizer_type']}")
        logger.info(f"  Status: {registry_info['status']}")
        logger.info(f"  MCP URL: {registry_info['mcp_url']}")
        logger.info(f"  Control Service: {registry_info['control_service']}")
        logger.info(f"  Control Endpoint: {registry_info['control_endpoint']}")
        logger.info(f"Total deployment time: {elapsed_time:.1f} seconds")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"Deployment Failed: {e}")
        import traceback

        logger.error(traceback.format_exc())
        raise
    finally:
        config_data = build_config(registry_info)
        if write_application_config(config_data):
            if deployment_success:
                logger.info(f"Updated {CONFIG_PATH}")
            else:
                logger.info(f"Saved partial deployment info to {CONFIG_PATH}")


if __name__ == "__main__":
    main()
