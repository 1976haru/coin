"""check_secret_policy.py — 체크리스트 #27 보조 스크립트.

repository 내 금지 secret 패턴과 위험 env 노출을 정적 스캔한다. 운영자가 PR 전에
수동 실행할 수 있게 한다.

사용:
    python scripts/check_secret_policy.py [--json]

종료 코드:
    0 — 모든 검사 통과
    1 — 위반 발견 (출력 메시지/JSON 참조)
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# 스캔 대상 디렉터리 — backend, frontend(src+env), docs, 루트 파일.
INCLUDE_DIRS = (
    REPO_ROOT / "backend",
    REPO_ROOT / "frontend" / "src",
    REPO_ROOT / "docs",
)
INCLUDE_FILES = (
    REPO_ROOT / ".env.example",
    REPO_ROOT / "README.md",
    REPO_ROOT / "CLAUDE.md",
)
# config/.env.example 가 있을 때도 검사.
if (REPO_ROOT / "config" / ".env.example").exists():
    INCLUDE_FILES = INCLUDE_FILES + ((REPO_ROOT / "config" / ".env.example"),)

# 검사 제외 디렉터리 패턴 (부분 일치).
EXCLUDE_PATTERNS = (
    "__pycache__", "node_modules", ".git", "venv", ".venv",
    "dist", "build", "logs", ".pytest_cache",
)

# 검사할 파일 확장자 (텍스트로 안전하게 열 수 있는 것).
ALLOWED_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".env",
    ".md", ".txt", ".yaml", ".yml", ".toml",
}


def _is_excluded(path: Path) -> bool:
    parts = path.parts
    return any(any(ex == part or ex in part for ex in EXCLUDE_PATTERNS)
               for part in parts)


def _iter_target_files() -> list[Path]:
    out: list[Path] = []
    for d in INCLUDE_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if _is_excluded(p):
                continue
            if p.suffix not in ALLOWED_EXTS:
                continue
            out.append(p)
    for f in INCLUDE_FILES:
        if f.is_file() and not _is_excluded(f):
            out.append(f)
    return out


# ── 검사 항목 ──────────────────────────────────────────────────────


class Finding(dict):
    """경량 finding 컨테이너 — dict 호환."""


_TESTS_DIR_MARKER = ("tests", "test_phase10_scripts.py")


def _in_tests_dir(path: Path) -> bool:
    """tests/ 디렉터리 안의 파일은 부정-예시(fixture)를 담을 수 있어 일부 rule 제외."""
    name = path.name
    return "tests" in path.parts or name.startswith("test_")


# 1. 실제 키처럼 보이는 long token (변수=값 라인).
_LONG_SECRET_VAR_PAT = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Z][A-Z0-9_]*?(?:KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|API))"
    r"\s*=\s*[\"']?(?P<val>[A-Za-z0-9+/_\-]{20,})[\"']?\s*$",
    re.M,
)

# 2. inline assignment (Python/TS) 으로 secret 류.
_INLINE_SECRET_PAT = re.compile(
    r"""(?ix)
    (api[_-]?key|secret(?:_key)?|access[_-]?token|passphrase)
    \s* [:=] \s*
    ['\"]
    (?P<val>[A-Za-z0-9+/_\-]{20,})
    ['\"]
    """,
)


# 3. 위험 frontend env 변수.
_FRONTEND_DANGER_PAT = re.compile(
    r"VITE_[A-Z0-9_]*(SECRET|ACCESS_TOKEN|API_KEY|PASSPHRASE)"
    r"|NEXT_PUBLIC_[A-Z0-9_]*(SECRET|ACCESS_TOKEN|API_KEY|PASSPHRASE)"
    r"|UPBIT_SECRET_KEY|OKX_(?:API_)?(?:SECRET|PASSWORD|PASSPHRASE)"
    r"|BINANCE_(?:API_)?SECRET",
    re.I,
)


# 4. 실제 거래소 출금 endpoint URL.
_WITHDRAW_ENDPOINT_PATS = (
    re.compile(r"/v1/withdraws"),                # Upbit
    re.compile(r"/sapi/v1/capital/withdraw"),    # Binance
    re.compile(r"/api/v5/asset/withdrawal"),     # OKX
    re.compile(r"/wapi/v3/withdraw"),            # Binance legacy
)


# 5. withdraw 메서드 정의 패턴 — 단, 검증 helper 이름(`assert_no_withdrawal_methods`)
#    은 허용.
_WITHDRAW_DEF_PAT = re.compile(
    r"^\s*(?:async\s+)?def\s+(?P<name>withdraw|withdrawal|"
    r"transfer_to_external|send_to_address|create_withdrawal|"
    r"request_withdrawal)\s*\(",
    re.M,
)


# 6. dangerous flag 의 true 대입.
_DANGEROUS_TRUE_PAT = re.compile(
    r"^(ENABLE_LIVE_TRADING|ENABLE_AI_EXECUTION|ENABLE_CRYPTO_FUTURES_LIVE)"
    r"\s*=\s*[Tt][Rr][Uu][Ee]\s*$",
    re.M,
)


# placeholder / known fake — 허용 substring (값에 포함되면 finding 제외).
_PLACEHOLDER_TOKENS = (
    "SET_IN_LOCAL_ENV_ONLY",
    "PLACEHOLDER",
    "CHANGE_ME",
    "change-me",
    "your_",
    "YOUR_",
    "example.com",
    "REDACTED",
    "leaked-key-",     # 테스트 fixture
    "leaked-secret-",
    "leaked-pass-",
    "super-secret-key",
    "another-secret",
    "super-passphrase",
    "dummy_key",
    "dummy_sec",
    "test-",
    "fake-",
)


def _is_known_placeholder(value: str) -> bool:
    val = value.lower()
    return any(tok.lower() in val for tok in _PLACEHOLDER_TOKENS)


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan() -> list[Finding]:
    findings: list[Finding] = []
    targets = _iter_target_files()

    for path in targets:
        rel = path.relative_to(REPO_ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # 1. long secret variable assignment
        for m in _LONG_SECRET_VAR_PAT.finditer(text):
            val = m.group("val")
            if _is_known_placeholder(val):
                continue
            findings.append(Finding(
                file=rel, line=_line_no(text, m.start()),
                rule="long_secret_var",
                snippet=m.group(0).strip(),
            ))

        # 2. inline secret (Python/TS literal). 테스트 dir 은 부정-예시 fixture
        #    (스캐너 자체 검증용 가짜 secret) 를 포함하므로 제외.
        if not _in_tests_dir(path):
            for m in _INLINE_SECRET_PAT.finditer(text):
                val = m.group("val")
                if _is_known_placeholder(val):
                    continue
                findings.append(Finding(
                    file=rel, line=_line_no(text, m.start()),
                    rule="inline_secret",
                    snippet=m.group(0).strip(),
                ))

        # 3. frontend secret env literal — 정책 문서 / docs 는 *문서 목적* 으로
        #    이름이 등장하므로 허용. backend 검증 코드도 허용.
        if rel.startswith("frontend/"):
            for m in _FRONTEND_DANGER_PAT.finditer(text):
                findings.append(Finding(
                    file=rel, line=_line_no(text, m.start()),
                    rule="frontend_secret_env",
                    snippet=m.group(0),
                ))

        # 4. real withdraw endpoint URL — 정책 문서는 일부 언급 가능. 검사 시
        #    code 파일(.py/.ts/.tsx) 만 대상으로 한다. 테스트 파일은 부정-예시
        #    fixture (URL 부재 회귀) 를 포함해 제외.
        if path.suffix in {".py", ".ts", ".tsx", ".js", ".jsx"} and not _in_tests_dir(path):
            for pat in _WITHDRAW_ENDPOINT_PATS:
                for m in pat.finditer(text):
                    findings.append(Finding(
                        file=rel, line=_line_no(text, m.start()),
                        rule="withdraw_endpoint_url",
                        snippet=m.group(0),
                    ))
            # 5. withdraw method definition
            for m in _WITHDRAW_DEF_PAT.finditer(text):
                findings.append(Finding(
                    file=rel, line=_line_no(text, m.start()),
                    rule="withdraw_method_def",
                    snippet=m.group(0).strip(),
                ))

        # 6. dangerous flag true
        for m in _DANGEROUS_TRUE_PAT.finditer(text):
            findings.append(Finding(
                file=rel, line=_line_no(text, m.start()),
                rule="dangerous_flag_true",
                snippet=m.group(0).strip(),
            ))

    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="repository secret-policy scanner (#27).")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON instead of text")
    args = p.parse_args(argv)

    findings = scan()
    # Windows cp949 콘솔에서 em-dash 출력 실패 방지 — ASCII 만 사용.
    if args.json:
        out = {
            "findings_count": len(findings),
            "findings": findings,
        }
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if findings:
            print(f"FAIL - {len(findings)} secret-policy violation(s):")
            for f in findings:
                print(f"  {f['file']}:{f['line']}  [{f['rule']}]  {f['snippet'][:120]}")
        else:
            print("PASS - no secret-policy violations found.")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
