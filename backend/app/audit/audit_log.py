"""AuditLog — 메모리 + CSV 영속성, 자동 redaction 적용.

체크리스트 #11 Audit Foundation, #87 Audit Log.
이전 위치: app/storage/audit_log.py (Step A에서 audit/로 이동)

CLAUDE.md §2.1.3: secret/PII는 로그에 남지 않는다 — record() 시점에 redaction을
강제 적용해 메모리·CSV 양쪽 모두 마스킹된 사본만 보관한다.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .redaction import redact


class AuditLog:
    """메모리 + CSV 이중 저장. record/tail/count 인터페이스.

    record()는 페이로드를 redact()로 자동 정제한 뒤 저장한다. 호출자는
    원본을 그대로 넘기면 되며, 저장된 사본은 secret 패턴이 마스킹된 상태다.
    """

    def __init__(self, csv_path: str = "logs/audit_log.csv"):
        self.csv_path = Path(csv_path)
        self.events: list[dict] = []
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(["ts", "event_type", "payload_json"])

    def record(self, event_type: str, payload: dict) -> dict:
        safe_payload = redact(payload) if isinstance(payload, dict) else payload
        event = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "payload":    safe_payload,
        }
        self.events.append(event)
        try:
            with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow([
                    event["ts"],
                    event_type,
                    json.dumps(safe_payload, ensure_ascii=False, default=str),
                ])
        except Exception:
            pass
        return event

    def tail(self, limit: int = 100) -> list[dict]:
        return self.events[-limit:]

    def count(self) -> int:
        return len(self.events)

    def filter_by_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["event_type"] == event_type]


InMemoryAuditLog = AuditLog
