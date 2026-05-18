"""scripts/security_scan.py — 체크리스트 #93 Security Scan.

저장소에서 secret 패턴을 grep 으로 스캔한다. CI 보강용 + 로컬 사전 점검용.
.env 같은 파일이 실수로 staging 되지 않도록 빠른 사전 검증.

종료 코드:
    0  의심 패턴 없음
    1  secret 패턴 발견

사용:
    python scripts/security_scan.py
    python scripts/security_scan.py --json
    python scripts/security_scan.py --path frontend/
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(os.path.abspath(__file__)).parent
_ROOT = _HERE.parent

# 검색 대상 확장자
TEXT_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".html",
    ".css", ".md", ".yaml", ".yml", ".sh", ".ps1", ".env", ".ini",
}

# 절대 무시 디렉토리
IGNORE_DIRS = {
    "node_modules", "dist", ".git", "__pycache__", ".pytest_cache",
    ".venv", "venv", "_legacy_innogrit", ".vite", "build",
}

# 무시 파일
IGNORE_FILES = {
    ".env.example",            # 변수 카탈로그 — 값은 비워둠
    "package-lock.json",       # NPM hash — false positive 다수
}


# secret 패턴 (정규식)
SECRET_PATTERNS = [
    # API key 형식 (긴 영숫자)
    (r"(?i)(api[_-]?key|secret|passphrase|access[_-]?key)[\s]*[:=][\s]*['\"]?([A-Za-z0-9_\-]{20,})['\"]?",
     "api_key_pattern"),
    # Bearer token
    (r"(?i)\b(bearer|basic)\s+([A-Za-z0-9._\-]{20,})\b",
     "bearer_token"),
    # Telegram bot token
    (r"\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}\b",
     "telegram_bot_token"),
    # OKX/Upbit pattern (UUID-like)
    (r"(?i)(okx|upbit)_(api_key|secret_key|access_key|api_password)[\s]*[:=][\s]*['\"][A-Za-z0-9_\-]{20,}['\"]",
     "exchange_key"),
    # Anthropic / OpenAI key
    (r"\bsk-(ant-)?[A-Za-z0-9_\-]{30,}\b",
     "ai_provider_key"),
    # private key headers
    (r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
     "private_key_header"),
]

# 화이트리스트 — 명백한 placeholder
WHITELIST_VALUES = {
    "your_api_key_here",
    "change-me-local-only",
    "change_me",
    "REDACTED",
    "***REDACTED***",
    "YOUR_TOKEN_HERE",
    "__SET_IN_LOCAL_ENV_ONLY__",
    "set_in_local_env_only",
}


def _is_whitelisted(line: str) -> bool:
    low = line.lower()
    if "noqa: security-scan" in low:
        return True
    if "test" in low and "fixture" in low:
        return True
    if "example" in low and "placeholder" in low:
        return True
    for w in WHITELIST_VALUES:
        if w.lower() in low:
            return True
    return False


def scan(root: Path, *, target: Path | None = None) -> list[dict]:
    target = target or root
    findings = []
    for path in target.rglob("*"):
        if path.is_dir():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name in IGNORE_FILES:
            continue
        if path.suffix not in TEXT_EXTS and path.name != ".env.example":
            continue
        # 본 스크립트 자체와 정책 문서들은 패턴 정의를 포함하므로 스킵
        rel = path.relative_to(root)
        skip_paths = {
            "scripts/security_scan.py",
            "backend/app/audit/redaction.py",
            "docs/api_key_policy.md",
            "docs/sandbox_paper_keys.md",
            "docs/safety_principles.md",
            "docs/checklist_progress.md",
            "CLAUDE.md",
        }
        rel_str = str(rel).replace("\\", "/")
        if rel_str in skip_paths:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _is_whitelisted(line):
                continue
            for pat, name in SECRET_PATTERNS:
                m = re.search(pat, line)
                if m:
                    findings.append({
                        "file": rel_str,
                        "line": line_no,
                        "pattern": name,
                        "snippet": line.strip()[:120],
                    })
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Repo secret scanner")
    p.add_argument("--json", dest="as_json", action="store_true")
    p.add_argument("--path", default=None,
                   help="스캔할 경로 (기본: 저장소 루트)")
    args = p.parse_args(argv)

    target = Path(args.path) if args.path else _ROOT
    if not target.is_absolute():
        target = (_ROOT / target).resolve()

    findings = scan(_ROOT, target=target)

    if args.as_json:
        print(json.dumps({
            "scanned_path": str(target),
            "findings_count": len(findings),
            "findings": findings,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"Security Scan — 대상: {target}")
        print(f"발견: {len(findings)}건\n")
        for f in findings[:50]:
            print(f"  ❌ {f['file']}:{f['line']} [{f['pattern']}]")
            print(f"      {f['snippet']}")
        if len(findings) > 50:
            print(f"  ... 외 {len(findings) - 50}건")
        if not findings:
            print("✅ secret 패턴 없음")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
