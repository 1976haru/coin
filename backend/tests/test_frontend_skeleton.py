"""체크리스트 #7 Frontend Skeleton — 구조 회귀 테스트.

목적: Vite + React + TypeScript 스켈레톤의 핵심 파일과 폴더 구조가
기대대로 존재하는지 검증한다. npm install / 빌드는 별도 npm 스크립트에서.
"""
from __future__ import annotations
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND  = REPO_ROOT / "frontend"


# ── 1. 핵심 빌드 설정 파일 ────────────────────────────────────────

@pytest.mark.parametrize("relpath", [
    "package.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "index.html",
    ".gitignore",
])
def test_frontend_config_files_exist(relpath: str):
    assert (FRONTEND / relpath).is_file(), f"missing: frontend/{relpath}"


# ── 2. 폴더 구조 (체크리스트_mapping #7) ──────────────────────────

@pytest.mark.parametrize("subdir", [
    "src",
    "src/pages",
    "src/components",
    "src/api",
    "src/styles",
])
def test_frontend_directory_layout(subdir: str):
    assert (FRONTEND / subdir).is_dir(), f"missing dir: frontend/{subdir}"


# ── 3. React 진입점 + 페이지 ──────────────────────────────────────

@pytest.mark.parametrize("relpath", [
    "src/main.tsx",
    "src/App.tsx",
    "src/appInfo.ts",
    "src/api/client.ts",
    "src/api/health.ts",
    "src/api/watchlist.ts",
    "src/api/audit.ts",
    "src/api/approvals.ts",            # #74
    "src/api/risk.ts",                  # #75
    "src/components/Header.tsx",
    "src/components/StatusCard.tsx",
    "src/components/AdminTokenInput.tsx",     # #80
    "src/components/KillSwitchButton.tsx",     # #50/#75
    "src/components/ApprovalQueueWidget.tsx",  # #74
    "src/components/VersionWatcher.tsx",       # #83
    "src/contexts/AdminTokenContext.tsx",      # #80
    "src/pages/DashboardPage.tsx",
    "src/pages/WatchlistPage.tsx",
    "src/pages/AuditPage.tsx",
    "src/pages/ApprovalsPage.tsx",     # #74
    "src/pages/RiskPage.tsx",           # #75
    "src/styles/global.css",
    "public/manifest.json",             # #76 PWA
    "public/sw.js",                     # #76 PWA
])
def test_frontend_source_files_exist(relpath: str):
    assert (FRONTEND / relpath).is_file(), f"missing: frontend/{relpath}"


# ── 4. package.json 핵심 의존성/스크립트 ──────────────────────────

def test_package_json_has_required_scripts_and_deps():
    import json
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    for s in ("dev", "build", "preview", "typecheck"):
        assert s in pkg.get("scripts", {}), f"package.json scripts.{s} 누락"
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for d in ("react", "react-dom", "react-router-dom", "vite",
              "typescript", "@vitejs/plugin-react"):
        assert d in deps, f"package.json 의존성 누락: {d}"


# ── 5. 안전 회귀 — frontend 코드에 secret 패턴 부재 (CLAUDE.md §2.1.5) ──

@pytest.mark.parametrize("forbidden", [
    "ANTHROPIC_API_KEY", "OKX_API_SECRET", "UPBIT_SECRET_KEY",
    "TELEGRAM_TOKEN", "ADMIN_TOKEN=",
])
def test_frontend_source_has_no_committed_secrets(forbidden: str):
    """frontend src 에 secret 값/키 패턴이 하드코딩되지 않았는지."""
    src = FRONTEND / "src"
    for path in src.rglob("*"):
        if path.is_file() and path.suffix in {".ts", ".tsx", ".js", ".jsx", ".json", ".html", ".css"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert forbidden not in text, f"{path.relative_to(REPO_ROOT)} 에 금지 패턴 {forbidden!r} 발견"


# ── 6. 레거시 데모 HTML 보존 ──────────────────────────────────────

def test_legacy_demo_html_preserved():
    """기존 단일 HTML 데모는 legacy_demo.html 로 보존되어 있어야 함 (참고용)."""
    assert (FRONTEND / "legacy_demo.html").is_file()
