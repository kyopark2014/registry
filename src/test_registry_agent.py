#!/usr/bin/env python3
"""
End-to-end Agent Registry test for the harness_work Harness agent.

Flow:
  1) Search discoverable registry records for the harness agent
  2) Extract Harness ARN from the AGENT record (agent card / config fallback)
  3) Call bedrock-agentcore InvokeHarness and stream the response

Example:
  python3 src/test_registry_agent.py
  python3 src/test_registry_agent.py --prompt "안녕, 짧게 자기소개해줘"
  python3 src/test_registry_agent.py --search "harness_work" --actor-id ksdyb
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config

WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WORKING_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "application", "config.json")
HARNESS_CONFIG_PATH = os.path.abspath(
    os.path.join(PROJECT_ROOT, "..", "harness-work", "application", "config.json")
)

DEFAULT_SEARCH = "harness_work AgentCore Harness assistant"
DEFAULT_PROMPT = "짧게 자기소개하고, 어떤 도구를 쓸 수 있는지 한 줄로 말해줘."
DEFAULT_ACTOR_ID = "ksdyb"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("test_registry_agent")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_harness_config() -> Dict[str, Any]:
    if not os.path.isfile(HARNESS_CONFIG_PATH):
        return {}
    with open(HARNESS_CONFIG_PATH, "r") as f:
        return json.load(f)


def data_plane_client(region: str):
    return boto3.client(
        "agent-registry",
        region_name=region,
        endpoint_url=f"https://agent-registry.{region}.api.aws",
    )


def search_registry(
    *,
    region: str,
    registry_id: str,
    search_query: str,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    client = data_plane_client(region)
    response = client.search_discoverable_registry_records(
        registryIds=[registry_id],
        searchQuery=search_query,
        maxResults=max_results,
    )
    return response.get("registryRecords", [])


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def extract_agent_card(record: Dict[str, Any]) -> Dict[str, Any]:
    descriptors = record.get("descriptors") or {}
    block = descriptors.get("a2aAgentCard") or descriptors.get("a2a") or {}
    raw = block.get("data") or (block.get("agentCard") or {}).get("inlineContent")
    parsed = _parse_jsonish(raw) if raw else {}
    return parsed if isinstance(parsed, dict) else {}


def extract_harness_arn(record: Dict[str, Any]) -> Optional[str]:
    """Resolve Harness ARN from agent card url / metadata."""
    card = extract_agent_card(record)
    meta = card.get("metadata") if isinstance(card.get("metadata"), dict) else {}
    for candidate in (
        meta.get("harnessArn"),
        card.get("url"),
        record.get("harnessArn"),
    ):
        if isinstance(candidate, str) and candidate.startswith(
            "arn:aws:bedrock-agentcore:"
        ) and ":harness/" in candidate:
            return candidate
    return None


def pick_harness_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        raise RuntimeError("No discoverable registry records matched the search query")
    for record in records:
        name = (record.get("name") or "").lower()
        display = (record.get("displayName") or "").lower()
        rtype = (record.get("recordType") or "").upper()
        if rtype == "AGENT" and (
            "harness" in name or "harness" in display or name == "harness-work"
        ):
            return record
        if extract_harness_arn(record):
            return record
    for record in records:
        if (record.get("recordType") or "").upper() == "AGENT":
            return record
    return records[0]


def invoke_harness(
    *,
    harness_arn: str,
    region: str,
    prompt: str,
    actor_id: str,
    session_id: str,
) -> str:
    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        config=Config(
            read_timeout=300,
            connect_timeout=60,
            retries={"max_attempts": 0},
        ),
    )
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        actorId=actor_id,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
    )
    stream = response.get("stream")
    if stream is None:
        raise RuntimeError(f"Empty Harness response: {response}")

    chunks: List[str] = []
    print("--- InvokeHarness stream ---", flush=True)
    for event in stream:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            text = delta.get("text")
            if text:
                chunks.append(text)
                print(text, end="", flush=True)
        elif "messageStop" in event:
            print(
                f"\n\n[Stop reason: {event['messageStop'].get('stopReason')}]",
                flush=True,
            )
        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            print(
                f"\n[Tokens - input: {usage.get('inputTokens')}, "
                f"output: {usage.get('outputTokens')}]",
                flush=True,
            )
        elif "runtimeClientError" in event:
            msg = event["runtimeClientError"].get("message")
            print(f"\n[Error]: {msg}", flush=True)
            raise RuntimeError(msg or "runtimeClientError")
        else:
            keys = [k for k in event.keys() if not k.startswith("_")]
            if keys:
                print(f"\n[{', '.join(keys)}]", flush=True)
    print(flush=True)
    return "".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test Agent Registry discovery + InvokeHarness for harness_work"
    )
    parser.add_argument(
        "--search",
        default=DEFAULT_SEARCH,
        help="Registry search query used to discover the AGENT record",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="User prompt passed to InvokeHarness",
    )
    parser.add_argument(
        "--actor-id",
        default=DEFAULT_ACTOR_ID,
        help="InvokeHarness actorId (memory / ownership isolation)",
    )
    parser.add_argument(
        "--session-id",
        default="",
        help="runtimeSessionId (default: random UUID)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    harness_cfg = load_harness_config()
    region = config.get("region") or harness_cfg.get("region") or "us-west-2"
    registry_id = config["registry_id"]
    session_id = (args.session_id or "").strip() or str(uuid.uuid4())

    logger.info("=" * 60)
    logger.info("Agent Registry → Harness Agent E2E Test")
    logger.info("=" * 60)
    logger.info("Registry ID: %s", registry_id)
    logger.info("Search: %s", args.search)
    logger.info("Actor ID: %s", args.actor_id)
    logger.info("Session ID: %s", session_id)
    logger.info("Prompt: %s", args.prompt)

    logger.info("[1/3] Searching discoverable registry records")
    records = search_registry(
        region=region,
        registry_id=registry_id,
        search_query=args.search,
    )
    for i, record in enumerate(records, 1):
        card = extract_agent_card(record)
        logger.info(
            "  %d) %s (%s) type=%s card.name=%s",
            i,
            record.get("displayName") or record.get("name"),
            record.get("name"),
            record.get("recordType"),
            card.get("name"),
        )

    record = pick_harness_record(records)
    harness_arn = (
        extract_harness_arn(record)
        or config.get("harness_agent_harness_arn")
        or harness_cfg.get("HARNESS_ARN")
    )
    if not harness_arn:
        raise RuntimeError(
            "Could not resolve Harness ARN from registry record / config. "
            "Run: python3 src/register_agent.py"
        )

    logger.info("[2/3] Selected record: %s", record.get("name"))
    logger.info("  Harness ARN: %s", harness_arn)
    card = extract_agent_card(record)
    if card.get("description"):
        logger.info("  Description: %s", (card.get("description") or "")[:160])

    logger.info("[3/3] Invoking Harness")
    text = invoke_harness(
        harness_arn=harness_arn,
        region=region,
        prompt=args.prompt,
        actor_id=args.actor_id,
        session_id=session_id,
    )

    logger.info("=" * 60)
    logger.info("InvokeHarness completed (%d chars)", len(text))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
