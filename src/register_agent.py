#!/usr/bin/env python3
"""
Register harness-work AgentCore Harness (harness_work) into Agent Registry.

Reads harness metadata from harness-work/application/config.json (+ GetHarness),
ensures the project registry exists, then creates an AGENT registry record with
an A2A-style agent card that embeds the Harness ARN for discovery/invoke.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import register_mcp as mcp_reg  # noqa: E402
import registry as reg  # noqa: E402

RECORD_NAME = "harness-work"
RECORD_DISPLAY_NAME = "Harness Agent (harness_work)"
RECORD_VERSION = "1.0.0"
# A2A Agent Card schema accepted by Agent Registry (GA).
AGENT_CARD_SCHEMA_VERSION = "0.3.0"


def _load_json(path: str) -> Dict:
    with open(path, "r") as f:
        return json.load(f)


def _get_harness(harness_id: str, region: str) -> Dict[str, Any]:
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    return client.get_harness(harnessId=harness_id)["harness"]


def _tool_names(harness: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for tool in harness.get("tools") or []:
        name = tool.get("name")
        if name:
            names.append(name)
    for name in harness.get("allowedTools") or []:
        if isinstance(name, str) and name not in names:
            names.append(name)
    return names


def _build_agent_card(
    *,
    harness: Dict[str, Any],
    harness_arn: str,
    region: str,
    app_url: str = "",
    sharing_url: str = "",
) -> Dict[str, Any]:
    """Build an A2A Agent Card describing the managed AgentCore Harness."""
    harness_name = harness.get("harnessName") or "harness_work"
    tool_names = _tool_names(harness)
    model = harness.get("model") or {}
    model_id = (model.get("bedrockModelConfig") or {}).get("modelId", "")

    description = (
        "Managed AgentCore Harness for the harness-work project. "
        "Invoke via bedrock-agentcore InvokeHarness (streaming). "
        f"Default tools: {', '.join(tool_names) or 'shell, file_operations'}. "
        "Supports skills, remote MCP (exa, aws_knowledge, browser, code, project gateway), "
        "AgentCore Memory, and S3 Files workspace."
    )

    skills = [
        {
            "id": "general-assistant",
            "name": "General multi-tool assistant",
            "description": (
                "Answer questions and complete tasks using shell, files, browser, "
                "code interpreter, AWS knowledge, web search (exa), and project MCP gateway "
                "(Knowledge Base retrieve, artifact share)."
            ),
            "tags": ["harness", "agentcore", "mcp", "rag"],
            "examples": [
                "AWS Document를 이용하여 AgentCore Harness에 대해 조사하세요.",
                "보일러 에러 코드를 Knowledge Base에서 찾아 요약해 주세요.",
            ],
        }
    ]
    if any(t in tool_names for t in ("browser", "code", "aws_knowledge", "exa")):
        skills.append(
            {
                "id": "research-and-code",
                "name": "Research and code",
                "description": (
                    "Browse the web, search documentation, and run code in an isolated sandbox."
                ),
                "tags": ["browser", "code", "docs"],
                "examples": ["최신 AgentCore Registry 가격을 조사하고 표로 정리해 주세요."],
            }
        )

    # url holds the invoke target for this project's discovery convention:
    # AgentCore Harness ARN (not an HTTP A2A endpoint).
    card: Dict[str, Any] = {
        "name": harness_name,
        "description": description,
        "url": harness_arn,
        "provider": {
            "organization": "harness-work",
            "url": app_url or sharing_url or "https://aws.amazon.com/bedrock/agentcore/",
        },
        "version": RECORD_VERSION,
        "protocolVersion": AGENT_CARD_SCHEMA_VERSION,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": skills,
        # Non-standard hints for consumers (ignored by strict A2A clients).
        "supportsAuthenticatedExtendedCard": False,
        "metadata": {
            "invokeApi": "bedrock-agentcore:InvokeHarness",
            "harnessArn": harness_arn,
            "harnessId": harness.get("harnessId", ""),
            "harnessName": harness_name,
            "region": region,
            "modelId": model_id,
            "tools": tool_names,
            "projectName": "harness-work",
        },
    }
    return card


def _agent_descriptors(agent_card: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "a2aAgentCard": {
            "data": json.dumps(agent_card, ensure_ascii=False),
            "dataSchemaVersion": AGENT_CARD_SCHEMA_VERSION,
        }
    }


def create_or_get_agent_record(
    control_client,
    registry_id: str,
    *,
    agent_card: Dict[str, Any],
    description: str,
) -> Dict:
    """Create AGENT registry record (idempotent by name), then submit for approval."""
    existing = mcp_reg._find_record_by_name(control_client, registry_id, RECORD_NAME)
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
                if not mcp_reg._find_record_by_name(
                    control_client, registry_id, RECORD_NAME
                ):
                    break
                time.sleep(2)
            existing = None
        else:
            reg.logger.warning(
                "  Record already exists: %s (%s) status=%s — updating descriptors",
                RECORD_NAME,
                record_id,
                status,
            )
            try:
                control_client.update_registry_record(
                    registryId=registry_id,
                    recordId=record_id,
                    description=description,
                    displayName=RECORD_DISPLAY_NAME,
                    descriptors=_agent_descriptors(agent_card),
                )
            except ClientError as e:
                reg.logger.warning("  Update failed (will reuse existing): %s", e)
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
            "recordType": "AGENT",
            "recordVersion": RECORD_VERSION,
            "descriptors": _agent_descriptors(agent_card),
        }
        reg.logger.info("[2/3] Creating AGENT registry record (a2aAgentCard)")
        response = control_client.create_registry_record(**create_kwargs)
        record_id = mcp_reg._record_id_from_response(response)
        reg.logger.info("  ✓ Record created: %s", record_id)
        record = mcp_reg.wait_for_record_status(
            control_client,
            registry_id,
            record_id,
            desired_statuses=("DRAFT", "PENDING_APPROVAL", "APPROVED"),
        )

    record_id = record.get("recordId") or mcp_reg._record_id_from_response(record)
    status = record.get("status", "")
    if status in ("DRAFT", "PENDING_APPROVAL"):
        reg.logger.info("[3/3] Submitting record for approval")
        try:
            control_client.submit_registry_record_for_approval(
                registryId=registry_id,
                recordId=record_id,
            )
        except ClientError as e:
            reg.logger.warning("  Submit for approval: %s", e)
        record = mcp_reg.wait_for_record_status(
            control_client,
            registry_id,
            record_id,
            desired_statuses=("APPROVED", "PENDING_APPROVAL"),
        )
    elif status == "APPROVED":
        reg.logger.info("[3/3] Record already approved")

    return record


def main() -> None:
    reg.logger.info("=" * 60)
    reg.logger.info("Register harness_work Harness Agent into Agent Registry")
    reg.logger.info("=" * 60)

    if not os.path.isfile(mcp_reg.HARNESS_CONFIG_PATH):
        raise FileNotFoundError(
            f"harness-work config not found: {mcp_reg.HARNESS_CONFIG_PATH}"
        )

    harness_cfg = _load_json(mcp_reg.HARNESS_CONFIG_PATH)
    harness_id = harness_cfg.get("harnessId") or ""
    harness_arn = harness_cfg.get("HARNESS_ARN") or ""
    region = harness_cfg.get("region") or reg.region

    if not harness_id or not harness_arn:
        raise RuntimeError(
            "harness-work config missing harnessId / HARNESS_ARN — run installer.py first"
        )

    reg.logger.info("Harness config: %s", mcp_reg.HARNESS_CONFIG_PATH)
    reg.logger.info("Harness ID: %s", harness_id)
    reg.logger.info("Harness ARN: %s", harness_arn)

    harness = _get_harness(harness_id, region)
    if harness.get("status") != "READY":
        raise RuntimeError(
            f"Harness {harness_id} status is {harness.get('status')}, expected READY"
        )
    live_arn = harness.get("arn") or harness_arn
    if live_arn != harness_arn:
        reg.logger.warning(
            "  ARN mismatch: config=%s get_harness=%s (using get_harness)",
            harness_arn,
            live_arn,
        )
        harness_arn = live_arn

    agent_card = _build_agent_card(
        harness=harness,
        harness_arn=harness_arn,
        region=region,
        app_url=harness_cfg.get("app_url", ""),
        sharing_url=harness_cfg.get("sharing_url", ""),
    )
    description = (
        f"AgentCore Harness agent '{harness.get('harnessName')}' "
        f"(harnessId={harness_id}) from harness-work. "
        "Discover via Agent Registry, invoke with InvokeHarness."
    )

    reg.logger.info("[1/3] Ensuring Agent Registry exists")
    registry_info = mcp_reg._ensure_registry()
    registry_id = registry_info["registry_id"]
    control_client, _ = reg._create_control_client()

    record = create_or_get_agent_record(
        control_client,
        registry_id,
        agent_card=agent_card,
        description=description,
    )

    record_id = record.get("recordId", "")
    record_arn = record.get("recordArn", "")
    record_payload = {
        "harness_agent_record_name": RECORD_NAME,
        "harness_agent_record_id": record_id,
        "harness_agent_record_arn": record_arn,
        "harness_agent_record_status": record.get("status", ""),
        "harness_agent_name": harness.get("harnessName", "harness_work"),
        "harness_agent_harness_id": harness_id,
        "harness_agent_harness_arn": harness_arn,
        "harness_agent_region": region,
    }
    reg.write_application_config({**reg.build_config(registry_info), **record_payload})

    reg.logger.info("")
    reg.logger.info("=" * 60)
    reg.logger.info("Harness Agent registration completed")
    reg.logger.info("=" * 60)
    reg.logger.info("  Registry ID: %s", registry_id)
    reg.logger.info("  Record Name: %s", RECORD_NAME)
    reg.logger.info("  Record ID: %s", record_id)
    reg.logger.info("  Record ARN: %s", record_arn)
    reg.logger.info("  Status: %s", record.get("status"))
    reg.logger.info("  Harness ARN: %s", harness_arn)
    reg.logger.info("  Config: %s", reg.CONFIG_PATH)
    reg.logger.info("=" * 60)


if __name__ == "__main__":
    main()
