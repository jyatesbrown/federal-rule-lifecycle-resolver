"""Regenerate .actor/dataset_schema.json from the Pydantic output model (single source of truth)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.output import ResolutionResult  # noqa: E402

SCHEMA_PATH = ROOT / ".actor" / "dataset_schema.json"


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_inline(v, defs) for v in node]
    return node


def build_fields() -> dict[str, Any]:
    schema = ResolutionResult.model_json_schema(by_alias=True, mode="serialization")
    defs = schema.pop("$defs", {})
    return _inline(schema, defs)


def _view(title: str, description: str, fields: dict[str, tuple[str, str]], **transformation: Any) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "transformation": {**transformation, "fields": list(fields)},
        "display": {
            "component": "table",
            "properties": {k: {"label": label, "format": fmt} for k, (label, fmt) in fields.items()},
        },
    }


def build_schema() -> dict[str, Any]:
    return {
        "actorSpecification": 1,
        "fields": build_fields(),
        "views": {
            "overview": _view(
                "Overview",
                "Resolution status and reconstructed lifecycle.",
                {
                    "status": ("Status", "text"),
                    "action.canonicalTitle": ("Rulemaking", "text"),
                    "action.rins": ("RINs", "array"),
                    "action.docketIds": ("Dockets", "array"),
                    "lifecycle.currentStage": ("Stage", "text"),
                    "lifecycle.commentPeriod.currentDeadline": ("Comment deadline", "text"),
                    "lifecycle.effectiveDate.current": ("Effective date", "text"),
                    "lifecycle.latestOfficialActionDate": ("Latest action", "text"),
                    "resolution.confidence": ("Confidence", "text"),
                    "billing.billable": ("Billable", "boolean"),
                    "checkedAt": ("Checked at", "date"),
                },
                flatten=["action", "lifecycle", "resolution", "billing"],
            ),
            "documents": _view(
                "Related documents",
                "One row per official Federal Register document in the lifecycle.",
                {
                    "documentNumber": ("FR document", "text"),
                    "normalizedType": ("Type", "text"),
                    "nativeAction": ("Official action", "text"),
                    "publicationDate": ("Published", "text"),
                    "relationBasis": ("Relation basis", "array"),
                    "sourceUrl": ("URL", "link"),
                },
                unwind=["relatedDocuments"],
            ),
        },
    }


def main() -> None:
    SCHEMA_PATH.write_text(json.dumps(build_schema(), indent=4, sort_keys=False) + "\n")
    print(f"wrote {SCHEMA_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
