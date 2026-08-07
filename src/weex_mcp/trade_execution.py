from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


# Retained as a protocol literal for compatibility with existing trade clients.
CONFIRMATION_REPLY_TEXT = "确认"


@dataclass(frozen=True)
class TradeExecution:
    action: str
    market: str
    trading_mode: str
    summary: dict[str, Any]
    risk: dict[str, Any]
    risk_signature: str


def canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()
