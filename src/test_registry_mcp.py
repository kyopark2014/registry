#!/usr/bin/env python3
"""
End-to-end Agent Registry test.

Flow:
  1) Search discoverable registry records (GA agent-registry data plane)
  2) Extract MCP URL from the matched record
  3) Call the MCP retrieve tool with SigV4 (AgentCore Runtime)

Example:
  python3 src/test_registry_mcp.py
  python3 src/test_registry_mcp.py --query "보일러 에러 코드" --actor-id ksdyb
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
import httpx
from botocore.auth import SigV4Auth as BotocoreSigV4Auth
from botocore.awsrequest import AWSRequest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WORKING_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "application", "config.json")

DEFAULT_SEARCH = "knowledge base retrieve RAG"
DEFAULT_QUERY = "보일러 에러 코드"
DEFAULT_ACTOR_ID = "ksdyb"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("test_registry")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r") as f:
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


def extract_mcp_url(record: Dict[str, Any]) -> Optional[str]:
    """Pull streamable-http URL from mcpServer descriptors."""
    descriptors = record.get("descriptors") or {}
    mcp_server = descriptors.get("mcpServer") or {}

    data = mcp_server.get("data")
    if data:
        try:
            payload = json.loads(data) if isinstance(data, str) else data
            for remote in payload.get("remotes") or []:
                if remote.get("url"):
                    return remote["url"]
        except json.JSONDecodeError:
            pass

    source = ((mcp_server.get("source") or {}).get("fromUrl") or {})
    if source.get("url"):
        return source["url"]

    return record.get("mcpServerUrl") or record.get("url")


def extract_tool_names(record: Dict[str, Any]) -> List[str]:
    descriptors = record.get("descriptors") or {}
    mcp_server = descriptors.get("mcpServer") or {}
    tools_block = ((mcp_server.get("additionalData") or {}).get("tools") or {})
    raw = tools_block.get("data")
    if not raw:
        return []
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict) and "tools" in payload:
        tools = payload["tools"]
    else:
        tools = payload
    names = []
    for tool in tools or []:
        if isinstance(tool, dict) and tool.get("name"):
            names.append(tool["name"])
    return names


def _sigv4_region_for_url(url: str, fallback: str) -> str:
    host = urlparse(url).netloc
    parts = host.split(".")
    try:
        idx = parts.index("bedrock-agentcore")
        if idx + 1 < len(parts) and parts[idx + 1] != "amazonaws":
            return parts[idx + 1]
    except ValueError:
        pass
    try:
        idx = parts.index("agent-registry")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    except ValueError:
        pass
    return fallback


def install_sigv4_httpx_patch(default_region: str) -> None:
    """Monkey-patch httpx.AsyncClient to SigV4-sign AgentCore / Registry MCP calls."""
    if getattr(httpx.AsyncClient, "_registry_sigv4_patched", False):
        return

    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        async def sign_request(request: httpx.Request) -> None:
            url_str = str(request.url)
            if request.headers.get("Authorization"):
                return

            if "bedrock-agentcore" in url_str:
                service = "bedrock-agentcore"
            elif "agent-registry" in url_str:
                service = "agent-registry"
            else:
                return

            boto_session = boto3.Session()
            credentials = boto_session.get_credentials().get_frozen_credentials()
            parsed_url = urlparse(url_str)
            host = parsed_url.netloc
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

            body = None
            if request.content:
                body = (
                    request.content
                    if isinstance(request.content, bytes)
                    else await request.aread()
                )
                if hasattr(request, "_content"):
                    request._content = body

            aws_headers = {
                "host": host,
                "x-amz-date": timestamp,
                "Content-Type": request.headers.get(
                    "Content-Type", "application/json"
                ),
                "Accept": request.headers.get(
                    "Accept", "application/json, text/event-stream"
                ),
            }
            if body:
                aws_headers["Content-Length"] = str(len(body))

            aws_request = AWSRequest(
                method=request.method,
                url=url_str,
                headers=aws_headers,
                data=body,
            )
            region = _sigv4_region_for_url(url_str, default_region)
            BotocoreSigV4Auth(credentials, service, region).add_auth(aws_request)

            request.headers["X-Amz-Date"] = timestamp
            request.headers["Authorization"] = aws_request.headers["Authorization"]
            if credentials.token:
                request.headers["X-Amz-Security-Token"] = credentials.token

        if "event_hooks" not in kwargs:
            kwargs["event_hooks"] = {"request": [], "response": []}
        elif not isinstance(kwargs["event_hooks"], dict):
            kwargs["event_hooks"] = {"request": [], "response": []}
        if "request" not in kwargs["event_hooks"]:
            kwargs["event_hooks"]["request"] = []
        kwargs["event_hooks"]["request"].append(sign_request)
        original_init(self, *args, **kwargs)

    httpx.AsyncClient.__init__ = patched_init
    httpx.AsyncClient._registry_sigv4_patched = True
    logger.info("Applied httpx SigV4 patch for AgentCore / Agent Registry MCP")


def ensure_runtime_invoke_principal(runtime_arn: str, region: str) -> None:
    """Allow the current IAM principal to InvokeAgentRuntime on the MCP runtime."""
    sts = boto3.client("sts", region_name=region)
    identity = sts.get_caller_identity()
    caller_arn = identity["Arn"]
    # Convert assumed-role session ARN to role ARN when needed.
    if ":assumed-role/" in caller_arn:
        role_name = caller_arn.split(":assumed-role/")[1].split("/")[0]
        principal = f"arn:aws:iam::{identity['Account']}:role/{role_name}"
    else:
        principal = caller_arn

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        existing = control.get_resource_policy(resourceArn=runtime_arn)
        policy = json.loads(existing.get("policy") or "{}")
    except Exception:
        policy = {"Version": "2012-10-17", "Statement": []}

    statements = policy.setdefault("Statement", [])
    principals: List[str] = []
    for stmt in statements:
        p = (stmt.get("Principal") or {}).get("AWS")
        if isinstance(p, str):
            principals.append(p)
        elif isinstance(p, list):
            principals.extend(p)

    if principal in principals:
        logger.info("Runtime resource policy already allows %s", principal)
        return

    principals.append(principal)
    # Keep unique order
    seen = set()
    uniq = []
    for p in principals:
        if p not in seen:
            seen.add(p)
            uniq.append(p)

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowInvokeAgentRuntimePrincipals",
                "Effect": "Allow",
                "Principal": {"AWS": uniq if len(uniq) > 1 else uniq[0]},
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:InvokeAgentRuntimeForUser",
                ],
                "Resource": runtime_arn,
            }
        ],
    }
    control.put_resource_policy(resourceArn=runtime_arn, policy=json.dumps(policy))
    logger.info("Updated runtime resource policy to allow %s", principal)


def _tool_result_text(result: Any) -> str:
    chunks = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
        else:
            chunks.append(str(item))
    return "\n".join(chunks) if chunks else str(result)


async def call_retrieve(
    mcp_url: str,
    *,
    keyword: str,
    actor_id: str,
) -> str:
    async with streamablehttp_client(mcp_url) as streams:
        # mcp>=1.x may return (read, write) or (read, write, get_session_id)
        if len(streams) == 2:
            read, write = streams
        else:
            read, write, _ = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            logger.info("MCP tools: %s", tool_names)
            if "retrieve" not in tool_names:
                raise RuntimeError(f"retrieve tool not found. available={tool_names}")

            result = await session.call_tool(
                "retrieve",
                {"keyword": keyword, "actor_id": actor_id},
            )
            return _tool_result_text(result)


def pick_record(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not records:
        raise RuntimeError("No discoverable registry records matched the search query")
    for record in records:
        tools = extract_tool_names(record)
        if "retrieve" in tools or "knowledge" in (record.get("name") or "").lower():
            return record
    return records[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test Agent Registry + KB MCP flow")
    parser.add_argument(
        "--search",
        default=DEFAULT_SEARCH,
        help="Registry search query used to discover the MCP record",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help="Keyword passed to the retrieve tool",
    )
    parser.add_argument(
        "--actor-id",
        default=DEFAULT_ACTOR_ID,
        help="KB owner actor_id (account login id)",
    )
    parser.add_argument(
        "--skip-runtime-policy",
        action="store_true",
        help="Do not update AgentCore Runtime resource policy",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    region = config.get("region", "us-west-2")
    registry_id = config["registry_id"]
    runtime_arn = config.get("kb_mcp_runtime_arn", "")

    logger.info("=" * 60)
    logger.info("Agent Registry E2E Test")
    logger.info("=" * 60)
    logger.info("Registry ID: %s", registry_id)
    logger.info("Search: %s", args.search)
    logger.info("Retrieve query: %s", args.query)
    logger.info("Actor ID: %s", args.actor_id)

    logger.info("[1/3] Searching discoverable registry records")
    records = search_registry(
        region=region,
        registry_id=registry_id,
        search_query=args.search,
    )
    for i, record in enumerate(records, 1):
        logger.info(
            "  %d) %s (%s) type=%s tools=%s",
            i,
            record.get("displayName") or record.get("name"),
            record.get("name"),
            record.get("recordType"),
            extract_tool_names(record),
        )

    record = pick_record(records)
    mcp_url = extract_mcp_url(record) or config.get("kb_mcp_url")
    if not mcp_url:
        raise RuntimeError("Could not resolve MCP URL from registry record / config")

    logger.info("[2/3] Selected record: %s", record.get("name"))
    logger.info("  MCP URL: %s", mcp_url)

    if runtime_arn and not args.skip_runtime_policy:
        ensure_runtime_invoke_principal(runtime_arn, region)

    install_sigv4_httpx_patch(region)

    logger.info("[3/3] Calling retrieve(keyword=%r, actor_id=%r)", args.query, args.actor_id)
    result = asyncio.run(
        call_retrieve(mcp_url, keyword=args.query, actor_id=args.actor_id)
    )

    logger.info("=" * 60)
    logger.info("Retrieve result (MCP / actor_id filter)")
    logger.info("=" * 60)
    parsed: Any = None
    try:
        parsed = json.loads(result)
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except Exception:
        print(result)
        parsed = result

    # KB docs under docs/ without owner metadata won't match actor_id filter.
    if parsed == [] or parsed == "[]":
        logger.warning(
            "MCP retrieve returned no hits for actor_id=%r. "
            "harness-work KB filters by metadata owner; "
            "docs without owner (e.g. docs/error_code.pdf) are excluded.",
            args.actor_id,
        )
        logger.info("Fallback: unfiltered Bedrock retrieve for demo")
        kb_id = config.get("kb_knowledge_base_id") or config.get("knowledge_base_id")
        if kb_id:
            br = boto3.client("bedrock-agent-runtime", region_name=region)
            resp = br.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={"text": args.query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": 5}
                },
            )
            hits = []
            for item in resp.get("retrievalResults", []):
                hits.append(
                    {
                        "score": item.get("score"),
                        "contents": (item.get("content") or {}).get("text", ""),
                        "metadata": item.get("metadata"),
                    }
                )
            print(json.dumps(hits, ensure_ascii=False, indent=2))
        else:
            logger.warning("No knowledge_base_id in config; skip fallback retrieve")


if __name__ == "__main__":
    main()
