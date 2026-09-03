#!/usr/bin/env python3
"""
AWS Agent Registry uninstaller.

Deletes Agent Registry resources created by installer.py / register_*.py:
  - GA (agent-registry-control) registries and their records
  - Preview (bedrock-agentcore-control) registries and their records (legacy)

Reads targets from application/config.json when present, and also deletes any
registry whose name matches the configured project / registry name.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

# Keep in sync with installer.py
project_name = "registry-harness"
region = "us-west-2"

WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(WORKING_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "application", "config.json")

DELETE_WAIT_TIMEOUT_SEC = int(os.environ.get("AGENT_REGISTRY_DELETE_WAIT_TIMEOUT_SEC", "600"))
DELETE_POLL_INTERVAL_SEC = float(os.environ.get("AGENT_REGISTRY_DELETE_POLL_INTERVAL_SEC", "5"))

CONTROL_CANDIDATES = (
    {
        "label": "GA",
        "service_name": "agent-registry-control",
        "endpoint_url": f"https://agent-registry-control.{region}.api.aws",
    },
    {
        "label": "preview",
        "service_name": "bedrock-agentcore-control",
        "endpoint_url": None,
    },
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("uninstaller")


def _load_config() -> Dict:
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("Could not read %s: %s", CONFIG_PATH, e)
        return {}


def _write_config(config: Dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        logger.info("Updated %s", CONFIG_PATH)
    except Exception as e:
        logger.warning("Could not write %s: %s", CONFIG_PATH, e)


def _clear_registry_keys(config: Dict) -> Dict:
    """Remove registry / record keys from config after uninstall."""
    drop_prefixes = (
        "registry_",
        "kb_mcp_",
        "kb_knowledge_",
        "harness_agent_",
    )
    keep = {
        k: v
        for k, v in config.items()
        if not any(k == p or k.startswith(p) for p in drop_prefixes)
        and k not in ("registry_id", "registry_arn", "registry_name")
    }
    # Preserve account/region/project if present; projectName will be refreshed on install.
    return keep


def _make_client(candidate: Dict) -> Optional[object]:
    kwargs = {"region_name": region}
    if candidate.get("endpoint_url"):
        kwargs["endpoint_url"] = candidate["endpoint_url"]
    try:
        return boto3.client(candidate["service_name"], **kwargs)
    except Exception as e:
        logger.warning(
            "Skipping %s client (%s): %s",
            candidate["label"],
            candidate["service_name"],
            e,
        )
        return None


def _list_registries(client) -> List[Dict]:
    registries: List[Dict] = []
    next_token = None
    while True:
        kwargs = {}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_registries(**kwargs)
        except ClientError as e:
            logger.warning("list_registries failed: %s", e)
            return registries
        registries.extend(response.get("registries", response.get("registrySummaries", [])))
        next_token = response.get("nextToken")
        if not next_token:
            break
    return registries


def _list_records(client, registry_id: str) -> List[Dict]:
    records: List[Dict] = []
    next_token = None
    while True:
        kwargs = {"registryId": registry_id}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_registry_records(**kwargs)
        except ClientError as e:
            logger.warning("list_registry_records(%s) failed: %s", registry_id, e)
            return records
        records.extend(response.get("registryRecords", response.get("records", [])))
        next_token = response.get("nextToken")
        if not next_token:
            break
    return records


def _record_id(record: Dict) -> str:
    return record.get("recordId") or record.get("id") or ""


def _is_not_found(exc: ClientError) -> bool:
    code = (exc.response.get("Error") or {}).get("Code", "")
    msg = str(exc).lower()
    return code in (
        "ResourceNotFoundException",
        "NotFoundException",
        "ValidationException",
    ) or "not found" in msg or "does not exist" in msg


def delete_registry_records(client, registry_id: str) -> int:
    """Delete all records in a registry. Returns deleted count."""
    records = _list_records(client, registry_id)
    deleted = 0
    for record in records:
        rid = _record_id(record)
        if not rid:
            continue
        name = record.get("name", "")
        logger.info("  Deleting record %s (%s) status=%s", name, rid, record.get("status"))
        try:
            client.delete_registry_record(registryId=registry_id, recordId=rid)
            deleted += 1
        except ClientError as e:
            if _is_not_found(e):
                logger.info("  Record already gone: %s", rid)
            else:
                logger.warning("  Failed to delete record %s: %s", rid, e)
    # Brief wait so delete_registry is less likely to conflict
    if deleted:
        time.sleep(2)
    return deleted


def wait_until_registry_deleted(client, registry_id: str) -> bool:
    deadline = time.time() + DELETE_WAIT_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            response = client.get_registry(registryId=registry_id)
            registry = response.get("registry", response)
            status = registry.get("status", "")
            logger.info("  Waiting for registry %s status=%s", registry_id, status)
            if status in ("DELETED",):
                return True
        except ClientError as e:
            if _is_not_found(e):
                logger.info("  ✓ Registry deleted: %s", registry_id)
                return True
            logger.warning("  get_registry error: %s", e)
        time.sleep(DELETE_POLL_INTERVAL_SEC)
    logger.error("Timed out waiting for registry deletion: %s", registry_id)
    return False


def delete_registry(client, registry_id: str, *, label: str) -> bool:
    logger.info("[%s] Deleting registry %s", label, registry_id)
    delete_registry_records(client, registry_id)
    try:
        client.delete_registry(registryId=registry_id)
    except ClientError as e:
        if _is_not_found(e):
            logger.info("  Registry already gone: %s", registry_id)
            return True
        logger.error("  delete_registry failed: %s", e)
        return False
    return wait_until_registry_deleted(client, registry_id)


def collect_targets(
    client,
    *,
    config: Dict,
    names: List[str],
    also_config_id: bool,
) -> List[Tuple[str, str]]:
    """Return [(registry_id, name), ...] to delete (deduped)."""
    targets: Dict[str, str] = {}

    if also_config_id:
        cfg_id = (config.get("registry_id") or "").strip()
        if cfg_id:
            targets[cfg_id] = config.get("registry_name") or "(from config)"

    for registry in _list_registries(client):
        rid = registry.get("registryId") or ""
        rname = registry.get("name") or ""
        if not rid:
            continue
        if rname in names or rid in targets:
            targets[rid] = rname or targets.get(rid, "")

    return [(rid, name) for rid, name in targets.items()]


def uninstall(
    *,
    names: Optional[List[str]] = None,
    include_preview: bool = True,
    dry_run: bool = False,
) -> None:
    config = _load_config()
    # Always try current project name + legacy "registry" + config registry_name
    name_set = {
        project_name,
        "registry",
        (config.get("registry_name") or "").strip(),
        (config.get("projectName") or "").strip(),
    }
    if names:
        name_set.update(n.strip() for n in names if n and n.strip())
    name_list = sorted(n for n in name_set if n)

    logger.info("=" * 60)
    logger.info("Agent Registry Uninstaller")
    logger.info("=" * 60)
    logger.info("Region: %s", region)
    logger.info("Target names: %s", ", ".join(name_list))
    logger.info("Config: %s", CONFIG_PATH)
    logger.info("Dry run: %s", dry_run)
    logger.info("=" * 60)

    any_deleted = False
    for candidate in CONTROL_CANDIDATES:
        if candidate["label"] == "preview" and not include_preview:
            continue
        client = _make_client(candidate)
        if client is None:
            continue
        label = candidate["label"]
        logger.info("")
        logger.info("--- %s (%s) ---", label, candidate["service_name"])
        targets = collect_targets(
            client,
            config=config,
            names=name_list,
            also_config_id=(label == "GA"),
        )
        if not targets:
            logger.info("  No matching registries")
            continue
        for registry_id, name in targets:
            logger.info("  Target: name=%s id=%s", name, registry_id)
            if dry_run:
                records = _list_records(client, registry_id)
                logger.info("  [dry-run] would delete %d record(s) + registry", len(records))
                for rec in records:
                    logger.info(
                        "    - %s (%s) %s",
                        rec.get("name"),
                        rec.get("recordType"),
                        rec.get("status"),
                    )
                continue
            if delete_registry(client, registry_id, label=label):
                any_deleted = True

    if not dry_run:
        cleared = _clear_registry_keys(config)
        cleared["projectName"] = project_name
        cleared["region"] = region
        _write_config(cleared)

    logger.info("")
    logger.info("=" * 60)
    if dry_run:
        logger.info("Dry run complete (nothing deleted)")
    elif any_deleted:
        logger.info("Uninstall completed")
    else:
        logger.info("Uninstall finished (no registries deleted)")
    logger.info("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete Agent Registry resources")
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="Extra registry name to delete (repeatable). "
        f"Defaults always include '{project_name}' and legacy 'registry'.",
    )
    parser.add_argument(
        "--skip-preview",
        action="store_true",
        help="Do not touch bedrock-agentcore (preview) registries",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List matching registries/records without deleting",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        uninstall(
            names=args.name,
            include_preview=not args.skip_preview,
            dry_run=args.dry_run,
        )
    except Exception as e:
        logger.error("Uninstall failed: %s", e)
        import traceback

        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
