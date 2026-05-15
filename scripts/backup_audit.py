"""scripts/backup_audit.py — 체크리스트 #88 Backup.

audit_log.csv + (옵션) DB 파일을 timestamped 폴더에 복사. 매일 cron 으로 실행 권장.

사용:
    python scripts/backup_audit.py
    python scripts/backup_audit.py --dest backups/
    python scripts/backup_audit.py --keep-days 30
"""
from __future__ import annotations
import argparse
import os
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


_HERE = Path(os.path.abspath(__file__)).parent
_ROOT = _HERE.parent

DEFAULT_SOURCES = [
    "logs/audit_log.csv",
    "logs/agent_trader.db",   # DATABASE_URL 기본 SQLite
]


def make_backup(
    *,
    dest: Path,
    sources: list[Path],
    timestamp: str | None = None,
) -> tuple[Path, list[Path]]:
    ts = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_dir = dest / f"backup_{ts}"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for src in sources:
        if not src.is_file():
            continue
        target = target_dir / src.name
        shutil.copy2(src, target)
        copied.append(target)
    return target_dir, copied


def cleanup_old(dest: Path, keep_days: int) -> list[Path]:
    """keep_days 이상 오래된 backup_* 폴더 제거."""
    if keep_days <= 0:
        return []
    cutoff = time.time() - keep_days * 24 * 3600
    removed: list[Path] = []
    if not dest.is_dir():
        return removed
    for p in dest.iterdir():
        if not p.is_dir() or not p.name.startswith("backup_"):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p)
                removed.append(p)
        except OSError:
            pass
    return removed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Audit log + DB backup")
    p.add_argument("--dest", default="backups",
                   help="백업 대상 디렉토리 (기본: backups/)")
    p.add_argument("--keep-days", type=int, default=30,
                   help="이보다 오래된 백업은 삭제 (기본 30일, 0 = 비활성)")
    args = p.parse_args(argv)

    dest = Path(args.dest)
    if not dest.is_absolute():
        dest = (_ROOT / dest).resolve()

    sources = [(_ROOT / s).resolve() for s in DEFAULT_SOURCES]
    target, copied = make_backup(dest=dest, sources=sources)

    print(f"Backup: {target}")
    if copied:
        for c in copied:
            print(f"  ✓ {c.name} ({c.stat().st_size} bytes)")
    else:
        print("  (복사할 source 없음 — logs/ 디렉토리 확인)")

    removed = cleanup_old(dest, args.keep_days)
    if removed:
        print(f"\n오래된 백업 제거 ({args.keep_days}일 초과): {len(removed)}개")
        for r in removed[:5]:
            print(f"  - {r.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
