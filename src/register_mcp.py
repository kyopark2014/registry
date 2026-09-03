#!/usr/bin/env python3
"""
Register harness-work Knowledge Base MCP (AgentCore Runtime) into Agent Registry.

Reads MCP runtime metadata from harness-work/application/config.json,
ensures the project registry exists (GA agent-registry-control), then creates
an MCP registry record via IAM-authenticated URL synchronization.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

# Allow `python3 src/register_mcp.py` from repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import installer as reg  # noqa: E402

HARNESS_WORK_ROOT = os.path.abspath(
    os.path.join(reg.PROJECT_ROOT, "..", "harness-work")
)
HARNESS_CONFIG_PATH = os.path.join(HARNESS_WORK_ROOT, "application", "config.json")

RECORD_NAME = "knowledge-base-of-harness-work"
RECORD_DISPLAY_NAME = "Knowledge Base MCP (harness-work)"
RECORD_VERSION = "1.0.0"
RUNTIME_NAME = "knowledge_base_of_harness_work"


def _load_json(path: str) -> Dict:
    with open(path, "r") as f:
        return json.load(f)


def _ensure_registry() -> Dict[str, str]:
    """Create or reuse the project registry and refresh application/config.json."""
    control_client, service_name = reg._create_control_client()
    info = reg.create_registry(control_client, service_name)
    reg.write_application_config(reg.build_config(info))
    return info


def _verify_agent_runtime(runtime_name: str) -> Dict:
    """Confirm AgentCore Runtime exists and is READY."""
    client = boto3.client("bedrock-agentcore-control", region_name=reg.region)
    next_token = None
    while True:
        kwargs = {}
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_agent_runtimes(**kwargs)
        for item in response.get("agentRuntimes", []):
            if item.get("agentRuntimeName") == runtime_name:
                status = item.get("status")
                if status != "READY":
                    raise RuntimeError(
                        f"Agent Runtime {runtime_name} status is {status}, expected READY"
                    )
                reg.logger.info(
                    "  ✓ Agent Runtime found: %s (%s)",
                    runtime_name,
                    item.get("agentRuntimeId"),
                )
                return item
        next_token = response.get("nextToken")
        if not next_token:
            break
    raise RuntimeError(f"Agent Runtime not found: {runtime_name}")


def _list_registry_records(control_client, registry_id: str) -> List[Dict]:
    records: List[Dict] = []
    next_token = None
    while True:
        kwargs = {"registryId": registry_id}
        if next_token:
            kwargs["nextToken"] = next_token
        response = control_client.list_registry_records(**kwargs)
        records.extend(response.get("registryRecords", response.get("records", [])))
        next_token = response.get("nextToken")
        if not next_token:
            break
    return records


def _find_record_by_name(
    control_client, registry_id: str, name: str
) -> Optional[Dict]:
    for record in _list_registry_records(control_client, registry_id):
        if record.get("name") == name:
            return record
    return None


def wait_for_record_status(
    control_client,
    registry_id: str,
    record_id: str,
    desired_statuses: tuple[str, ...],
    timeout_seconds: int = 600,
) -> Dict:
    """Poll registry record until it reaches one of the desired statuses."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        record = control_client.get_registry_record(
            registryId=registry_id,
            recordId=record_id,
        )
        status = record.get("status", "")
        if status in desired_statuses:
            reg.logger.info("  Record status: %s", status)
            return record
        if status in ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            reason = record.get("statusReason", "")
            raise RuntimeError(
                f"Record {record_id} entered terminal status: {status}"
                + (f" ({reason})" if reason else "")
            )
        reg.logger.info("  Waiting for record (%s) status: %s", record_id, status)
        time.sleep(10)
    raise TimeoutError(
        f"Timed out waiting for record {record_id} in {desired_statuses}"
    )


def _iam_sync_descriptors(mcp_url: str, role_arn: str) -> Dict:
    """Build GA mcpServer descriptors that synchronize from an IAM-protected URL."""
    return {
        "mcpServer": {
            "source": {
                "fromUrl": {
                    "url": mcp_url,
                    "credentialProviderConfigurations": [
                        {
                            "credentialProviderType": "IAM",
                            "credentialProvider": {
                                "iamCredentialProvider": {
                                    "roleArn": role_arn,
                                    # SigV4 service for AgentCore Runtime MCP URL host.
                                    "service": "bedrock-agentcore",
                                    "region": reg.region,
                                }
                            },
                        }
                    ],
                }
            }
        }
    }


def _manual_mcp_descriptors(mcp_url: str, description: str) -> Dict:
    """Fallback descriptors when URL synchronization is unavailable."""
    server_def = {
        "name": "harness-work/knowledge-base",
        "description": description,
        "version": RECORD_VERSION,
        "remotes": [
            {
                "type": "streamable-http",
                "url": mcp_url,
            }
        ],
    }
    tools_def = [
        {
            "name": "retrieve",
            "description": (
                "Query the harness-work Knowledge Base with RAG. "
                "Only returns documents owned by the given actor_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search query text",
                    },
                    "actor_id": {
                        "type": "string",
                        "description": (
                            "Account login id from the system prompt "
                            "(KB owner metadata / docs/{actor_id}/)"
                        ),
                    },
                },
                "required": ["keyword", "actor_id"],
            },
        }
    ]
    return {
        "mcpServer": {
            "data": json.dumps(server_def),
            "dataSchemaVersion": "2025-12-11",
            "additionalData": {
                "tools": {
                    "data": json.dumps(tools_def),
                    "dataSchemaVersion": "2025-11-25",
                }
            },
        }
    }


def _record_id_from_response(response: Dict) -> str:
    if response.get("recordId"):
        return response["recordId"]
    arn = response.get("recordArn", "")
    return arn.rsplit("/", 1)[-1] if arn else ""


def create_or_get_mcp_record(
    control_client,
    registry_id: str,
    *,
    mcp_url: str,
    role_arn: str,
    description: str,
    runtime_arn: str = "",
) -> Dict:
    """Create MCP registry record (idempotent by name), then submit for approval."""
    existing = _find_record_by_name(control_client, registry_id, RECORD_NAME)
    if existing:
        record_id = existing.get("recordId") or existing.get("id")
        status = existing.get("status", "")
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            reg.logger.warning(
                "  Deleting failed record %s (%s)", record_id, status
            )
            control_client.delete_registry_record(
                registryId=registry_id, recordId=record_id
            )
            for _ in range(30):
                if not _find_record_by_name(
                    control_client, registry_id, RECORD_NAME
                ):
                    break
                time.sleep(2)
            existing = None
        else:
            reg.logger.warning(
                "  Record already exists: %s (%s) status=%s",
                RECORD_NAME,
                record_id,
                status,
            )
            record = control_client.get_registry_record(
                registryId=registry_id,
                recordId=record_id,
            )

    if not existing:
        create_kwargs = {
            "registryId": registry_id,
            "name": RECORD_NAME,
            "displayName": RECORD_DISPLAY_NAME,
            "description": description,
            "recordType": "MCP",
            "recordVersion": RECORD_VERSION,
            "descriptors": _iam_sync_descriptors(mcp_url, role_arn),
        }

        reg.logger.info("[2/3] Creating registry record via IAM URL sync")
        record = None
        try:
            response = control_client.create_registry_record(**create_kwargs)
            record_id = _record_id_from_response(response)
            reg.logger.info("  ✓ Record created (sync): %s", record_id)
            record = wait_for_record_status(
                control_client,
                registry_id,
                record_id,
                desired_statuses=("DRAFT", "PENDING_APPROVAL", "APPROVED"),
            )
        except (ClientError, RuntimeError) as e:
            reg.logger.warning("  IAM sync create failed: %s", e)
            failed = _find_record_by_name(control_client, registry_id, RECORD_NAME)
            if failed:
                fid = failed.get("recordId")
                reg.logger.info("  Deleting failed sync record: %s", fid)
                try:
                    control_client.delete_registry_record(
                        registryId=registry_id, recordId=fid
                    )
                    for _ in range(30):
                        if not _find_record_by_name(
                            control_client, registry_id, RECORD_NAME
                        ):
                            break
                        time.sleep(2)
                except ClientError as delete_error:
                    reg.logger.warning("  Could not delete failed record: %s", delete_error)

            reg.logger.info("  Falling back to manual MCP descriptors")
            create_kwargs["descriptors"] = _manual_mcp_descriptors(
                mcp_url, description
            )
            response = control_client.create_registry_record(**create_kwargs)
            record_id = _record_id_from_response(response)
            reg.logger.info("  ✓ Record created (manual): %s", record_id)
            record = wait_for_record_status(
                control_client,
                registry_id,
                record_id,
                desired_statuses=("DRAFT", "PENDING_APPROVAL", "APPROVED"),
            )

    record_id = record.get("recordId") or _record_id_from_response(record)
    status = record.get("status", "")
    if status in ("DRAFT", "PENDING_APPROVAL"):
        reg.logger.info("[3/3] Submitting record for approval")
        try:
            control_client.submit_registry_record_for_approval(
                registryId=registry_id,
                recordId=record_id,
            )
        except ClientError as e:
            # Auto-approval registries may reject duplicate submit.
            reg.logger.warning("  Submit for approval: %s", e)
        record = wait_for_record_status(
            control_client,
            registry_id,
            record_id,
            desired_statuses=("APPROVED", "PENDING_APPROVAL"),
        )
    elif status == "APPROVED":
        reg.logger.info("[3/3] Record already approved")

    return record


def main():
    reg.logger.info("=" * 60)
    reg.logger.info("Register Knowledge Base MCP into Agent Registry")
    reg.logger.info("=" * 60)

    if not os.path.isfile(HARNESS_CONFIG_PATH):
        raise FileNotFoundError(f"harness-work config not found: {HARNESS_CONFIG_PATH}")

    harness_cfg = _load_json(HARNESS_CONFIG_PATH)
    runtime_arn = harness_cfg.get("knowledge_base_mcp_runtime_arn", "")
    mcp_url = harness_cfg.get("knowledge_base_mcp_url", "")
    sync_role_arn = (
        f"arn:aws:iam::{harness_cfg.get('accountId', reg.account_id)}"
        f":role/role-agent-registry-sync-for-registry"
    )
    # Prefer dedicated sync role; fall back to harness execution role.
    iam = boto3.client("iam")
    try:
        iam.get_role(RoleName="role-agent-registry-sync-for-registry")
    except ClientError:
        sync_role_arn = harness_cfg.get(
            "executionRoleArn",
            harness_cfg.get("knowledge_base_mcp_role", ""),
        )
    kb_id = harness_cfg.get("knowledge_base_id", "")

    if not runtime_arn or not mcp_url:
        raise RuntimeError(
            "harness-work config missing knowledge_base_mcp_runtime_arn / "
            "knowledge_base_mcp_url"
        )

    reg.logger.info("Harness config: %s", HARNESS_CONFIG_PATH)
    reg.logger.info("Runtime ARN: %s", runtime_arn)
    reg.logger.info("MCP URL: %s", mcp_url)
    reg.logger.info("Sync role: %s", sync_role_arn)

    runtime = _verify_agent_runtime(RUNTIME_NAME)
    if runtime.get("agentRuntimeArn") != runtime_arn:
        reg.logger.warning(
            "  Runtime ARN mismatch: config=%s list=%s (using config URL)",
            runtime_arn,
            runtime.get("agentRuntimeArn"),
        )

    reg.logger.info("[1/3] Ensuring Agent Registry exists")
    registry_info = _ensure_registry()
    registry_id = registry_info["registry_id"]
    control_client, _ = reg._create_control_client()

    description = (
        "Harness-work Knowledge Base retrieve MCP on AgentCore Runtime. "
        f"Runtime={RUNTIME_NAME}, knowledge_base_id={kb_id}. "
        "Tool: retrieve(keyword, actor_id)."
    )

    record = create_or_get_mcp_record(
        control_client,
        registry_id,
        mcp_url=mcp_url,
        role_arn=sync_role_arn,
        description=description,
        runtime_arn=runtime_arn,
    )

    record_id = record.get("recordId", "")
    record_arn = record.get("recordArn", "")
    record_payload = {
        "kb_mcp_record_name": RECORD_NAME,
        "kb_mcp_record_id": record_id,
        "kb_mcp_record_arn": record_arn,
        "kb_mcp_record_status": record.get("status", ""),
        "kb_mcp_runtime_name": RUNTIME_NAME,
        "kb_mcp_runtime_arn": runtime_arn,
        "kb_mcp_url": mcp_url,
        "kb_mcp_sync_role_arn": sync_role_arn,
        "kb_knowledge_base_id": kb_id,
    }
    reg.write_application_config({**reg.build_config(registry_info), **record_payload})

    reg.logger.info("")
    reg.logger.info("=" * 60)
    reg.logger.info("Knowledge Base MCP registration completed")
    reg.logger.info("=" * 60)
    reg.logger.info("  Registry ID: %s", registry_id)
    reg.logger.info("  Record Name: %s", RECORD_NAME)
    reg.logger.info("  Record ID: %s", record_id)
    reg.logger.info("  Record ARN: %s", record_arn)
    reg.logger.info("  Status: %s", record.get("status"))
    reg.logger.info("  MCP URL: %s", mcp_url)
    reg.logger.info("  Config: %s", reg.CONFIG_PATH)
    reg.logger.info("=" * 60)


if __name__ == "__main__":
    main()
