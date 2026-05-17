"""Exchange Notice Collector — 체크리스트 #18.

거래소 공지(입출금 중단, 유의종목, 상장폐지, 신규상장, 점검 등)를 여러 source 에서
read-only 로 수집해 정규화하고, ``exchange_notice`` 테이블에 영속화한다.

설계 원칙 (CLAUDE.md §2):
  - 거래소 SDK / private endpoint / 주문 endpoint 직접 호출 금지.
  - 본 모듈은 read-only — 공지 텍스트와 메타데이터만 읽는다.
  - ``NoticeSource`` Protocol 추상화 — 실제 거래소 RSS/HTML adapter 는 후속 단계.
  - ``MockNoticeSource`` 로 외부 네트워크 없이 테스트 가능.
  - 공지 이벤트는 **후보 필터와 리스크 설명** 용도. 직접 주문 트리거 아님.

중복 제거:
  1순위 (exchange, notice_id) — 거래소가 식별자 제공 시 update.
  2순위 (exchange, content_hash) — 식별자 부재 시 본문 sha256 기반.
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ExchangeNotice


# ── 공지 타입 / Severity 분류 ────────────────────────────────────────

NOTICE_TYPES: tuple[str, ...] = (
    "DEPOSIT_WITHDRAWAL_SUSPENSION",
    "CAUTION",
    "DELISTING",
    "LISTING",
    "MAINTENANCE",
    "TRADING_SUSPENSION",
    "POLICY",
    "OTHER",
)

SEVERITIES: tuple[str, ...] = ("INFO", "WARNING", "HIGH", "CRITICAL")


# notice_type → 기본 severity. _classify_severity 가 본문에 따라 상향 가능.
_DEFAULT_SEVERITY: dict[str, str] = {
    "DEPOSIT_WITHDRAWAL_SUSPENSION": "HIGH",
    "CAUTION":                       "WARNING",
    "DELISTING":                     "CRITICAL",
    "LISTING":                       "INFO",
    "MAINTENANCE":                   "WARNING",
    "TRADING_SUSPENSION":            "CRITICAL",
    "POLICY":                        "INFO",
    "OTHER":                         "INFO",
}


# 한국어/영어 키워드 기반 분류. 우선순위 순서대로 평가.
# (notice_type, keywords...)
_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "DELISTING",
        ("상장폐지", "상장 폐지", "거래지원 종료", "거래 지원 종료",
         "delisting", "delist", "remove from listing", "termination of"),
    ),
    (
        "TRADING_SUSPENSION",
        ("거래 중단", "거래중단", "거래 일시 정지", "거래 일시정지",
         "trading suspension", "trading halt", "trade halt", "suspension of trading"),
    ),
    (
        "DEPOSIT_WITHDRAWAL_SUSPENSION",
        ("입출금 중단", "입출금중단", "입출금 일시 중단", "입출금 일시중단",
         "입금 중단", "출금 중단", "지갑 점검", "월렛 점검", "지갑점검",
         "deposit and withdrawal", "deposit/withdrawal", "wallet maintenance",
         "deposit suspension", "withdrawal suspension",
         "suspended deposit", "suspended withdrawal"),
    ),
    (
        "CAUTION",
        ("유의종목", "투자유의", "투자 유의", "유의 종목",
         "caution", "warning notice", "monitoring", "investment caution"),
    ),
    (
        "LISTING",
        ("신규 상장", "신규상장", "거래지원 개시", "거래 지원 개시",
         "new listing", "listing announcement", "will be listed"),
    ),
    (
        "MAINTENANCE",
        ("시스템 점검", "거래소 점검", "서버 점검", "정기 점검", "임시 점검",
         "maintenance", "system maintenance", "scheduled maintenance"),
    ),
    (
        "POLICY",
        ("수수료", "약관", "정책 변경", "정책변경", "이용약관",
         "fee schedule", "fee update", "terms of service", "policy update"),
    ),
)


# severity 상향 키워드 — 본문/제목에 있으면 한 단계 위로.
_SEVERITY_UP_KEYWORDS: tuple[str, ...] = (
    "긴급", "immediate", "urgent", "emergency", "즉시",
)

# severity 강제 CRITICAL.
_SEVERITY_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "상장폐지", "거래지원 종료", "delisting",
    "거래 중단", "trading suspension", "trading halt",
)


# 심볼 추출용 — BTC, ETH, XRP 같은 3~6글자 대문자 토큰.
_SYMBOL_PATTERN = re.compile(r"\b([A-Z]{2,8})\b")

# 거래소 공지 본문/제목에서 자주 등장하는 일반 단어는 심볼이 아님 — 제외.
_SYMBOL_BLACKLIST: frozenset[str] = frozenset({
    "API", "KYC", "AML", "USD", "USDT", "USDC", "KRW", "JPY", "EUR",
    "NEW", "OLD", "PRO", "CEO", "CTO", "ETF", "TVL", "FAQ",
    "AND", "OR", "THE", "ALL", "ANY", "FOR", "WITH", "OF",
    "P2P", "OTC", "DEX", "CEX", "NFT", "DAO", "DEFI",
    "UTC", "AM", "PM",
    "BTC", "ETH",  # 매우 흔해 false-positive 위험 — 명시적으로 source 가 제공해야 안전
})

# 흔한 코인 심볼 화이트리스트 — 본문에 단독 등장 시 추출 허용.
_SYMBOL_WHITELIST: frozenset[str] = frozenset({
    "BTC", "ETH", "XRP", "ADA", "SOL", "DOT", "DOGE", "TRX", "AVAX",
    "MATIC", "ATOM", "LINK", "LTC", "BCH", "ETC", "XLM", "NEAR",
    "FIL", "APT", "ARB", "OP", "SUI", "INJ", "TIA", "SEI", "STX",
})


# ── 타입 ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RawNotice:
    """수집기 → collector 전달용 원본 공지.

    필드 대부분 optional — source 마다 가용 정보가 다름.
    """

    exchange: str
    title: str
    notice_id: str | None = None
    url: str = ""
    category: str = ""
    published_at: datetime | None = None
    body: str = ""
    symbols: tuple[str, ...] = ()
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedNotice:
    """정규화 후 ExchangeNotice 로 영속화 직전의 객체."""

    exchange: str
    title: str
    notice_id: str | None
    url: str
    category: str
    notice_type: str
    severity: str
    body: str
    symbols: tuple[str, ...]
    published_at: datetime | None
    content_hash: str
    source_name: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class CollectResult:
    """collect_once 결과 요약."""

    fetched: int
    inserted: int
    updated: int
    skipped: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    notices: tuple[ExchangeNotice, ...] = ()


# ── Protocol / Mock Source ───────────────────────────────────────

@runtime_checkable
class NoticeSource(Protocol):
    """거래소 공지 source — read-only.

    구현체는 ``fetch_notices(exchange, since)`` 만 만족하면 된다.
    실제 거래소 adapter (RSS/HTML/공식 announcement endpoint) 는
    후속 단계에서 별도로 추가하며, 본 collector 는 source 를 import 하지 않는다.
    """

    name: str

    def fetch_notices(
        self,
        exchange: str,
        since: datetime | None = None,
    ) -> list[RawNotice]:
        ...


class MockNoticeSource:
    """결정론적 공지 source — 외부 네트워크 호출 없음.

    fixture 에는 8개 notice_type 을 최소 1개씩 포함하고, notice_id 가 있는 것과
    없는 것 (content_hash 기반 dedup 검증용) 을 섞어 둔다. 일부는 심볼 추출
    검증용으로 본문에 BTC/ETH/XRP 같은 화이트리스트 토큰을 포함한다.
    """

    name = "mock"

    def __init__(self, exchange: str = "mock", fixtures: list[RawNotice] | None = None):
        self.exchange = exchange
        self._fixtures: list[RawNotice] = (
            fixtures if fixtures is not None else self._default_fixtures(exchange)
        )

    @staticmethod
    def _default_fixtures(exchange: str) -> list[RawNotice]:
        # 결정론적 시각 — datetime.now 사용하지 않음. 테스트 안정성을 위해.
        base = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
        return [
            # 1. DEPOSIT_WITHDRAWAL_SUSPENSION — notice_id 보유
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-001",
                title="[안내] XRP 입출금 일시 중단 안내 (지갑 점검)",
                url="https://example.test/notice/001",
                category="안내",
                published_at=base,
                body="네트워크 지갑 점검을 위해 XRP 입출금이 일시 중단됩니다. (Mock fixture — 실제 공지 아님)",
                symbols=("XRP",),
            ),
            # 2. CAUTION — notice_id 없음 (content_hash dedup)
            RawNotice(
                exchange=exchange,
                title="[유의종목 지정] DOGE 투자유의 종목 지정 안내",
                url="https://example.test/notice/caution-doge",
                category="caution",
                published_at=base + timedelta(hours=1),
                body="유동성 급감으로 인해 DOGE 가 투자유의 종목으로 지정되었습니다. (Mock fixture)",
                symbols=("DOGE",),
            ),
            # 3. DELISTING — notice_id 보유
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-003",
                title="[상장폐지] LUNA 상장폐지 및 거래지원 종료 안내",
                url="https://example.test/notice/003",
                category="delisting",
                published_at=base + timedelta(hours=2),
                body="LUNA 토큰의 상장폐지 및 거래지원 종료를 안내합니다. (Mock fixture)",
                symbols=("LUNA",),
            ),
            # 4. LISTING — notice_id 보유
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-004",
                title="[신규 상장] APT 거래지원 개시 안내",
                url="https://example.test/notice/004",
                category="listing",
                published_at=base + timedelta(hours=3),
                body="APT 토큰의 신규 상장 및 거래지원 개시를 안내합니다. (Mock fixture)",
                symbols=("APT",),
            ),
            # 5. MAINTENANCE — 전체 거래소 점검 (symbol 없음)
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-005",
                title="[시스템 점검] 정기 시스템 점검 안내",
                url="https://example.test/notice/005",
                category="maintenance",
                published_at=base + timedelta(hours=4),
                body="시스템 안정화를 위한 정기 점검을 진행합니다. (Mock fixture)",
            ),
            # 6. TRADING_SUSPENSION
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-006",
                title="[거래 중단] SOL 거래 일시 정지 안내",
                url="https://example.test/notice/006",
                category="trading_suspension",
                published_at=base + timedelta(hours=5),
                body="SOL 의 거래가 일시 정지됩니다. (Mock fixture)",
                symbols=("SOL",),
            ),
            # 7. POLICY
            RawNotice(
                exchange=exchange,
                notice_id="mock-2026-007",
                title="[정책] 수수료 정책 변경 안내",
                url="https://example.test/notice/007",
                category="policy",
                published_at=base + timedelta(hours=6),
                body="2026년 6월부터 적용되는 수수료 정책 변경을 안내합니다. (Mock fixture)",
            ),
            # 8. OTHER (분류 불가) — notice_id 없음
            RawNotice(
                exchange=exchange,
                title="[안내] 기타 일반 안내",
                url="https://example.test/notice/other",
                category="",
                published_at=base + timedelta(hours=7),
                body="이 공지는 어느 분류에도 해당하지 않는 mock fixture 입니다.",
            ),
        ]

    def fetch_notices(
        self,
        exchange: str,
        since: datetime | None = None,
    ) -> list[RawNotice]:
        out = [
            n for n in self._fixtures
            if n.exchange == exchange
        ]
        if since is not None:
            out = [
                n for n in out
                if n.published_at is None or n.published_at >= since
            ]
        return out


# ── 정규화 헬퍼 ────────────────────────────────────────────────────

def classify_notice_type(title: str, body: str = "", category: str = "") -> str:
    """제목/본문/카테고리에서 키워드로 notice_type 추론."""
    blob = " ".join([title or "", body or "", category or ""]).lower()
    for ntype, keywords in _TYPE_KEYWORDS:
        for kw in keywords:
            if kw.lower() in blob:
                return ntype
    return "OTHER"


def compute_severity(notice_type: str, title: str = "", body: str = "") -> str:
    """notice_type + 본문 키워드로 severity 결정."""
    base = _DEFAULT_SEVERITY.get(notice_type, "INFO")
    blob = (title + " " + body).lower()
    for kw in _SEVERITY_CRITICAL_KEYWORDS:
        if kw.lower() in blob:
            return "CRITICAL"
    if any(kw.lower() in blob for kw in _SEVERITY_UP_KEYWORDS):
        return _bump_severity(base)
    return base


def _bump_severity(s: str) -> str:
    order = {"INFO": "WARNING", "WARNING": "HIGH", "HIGH": "CRITICAL", "CRITICAL": "CRITICAL"}
    return order.get(s, s)


def extract_symbols(title: str, body: str = "") -> tuple[str, ...]:
    """본문/제목에서 코인 심볼 토큰 추출 (보수적 — whitelist 기반)."""
    blob = f"{title} {body}"
    found: list[str] = []
    seen: set[str] = set()
    for m in _SYMBOL_PATTERN.findall(blob):
        token = m.upper()
        if token in _SYMBOL_BLACKLIST:
            continue
        if token not in _SYMBOL_WHITELIST:
            continue
        if token in seen:
            continue
        seen.add(token)
        found.append(token)
    return tuple(found)


def compute_content_hash(exchange: str, title: str, body: str = "") -> str:
    """exchange/title/body 기반 sha256 — notice_id 부재 시 dedup 키."""
    norm_title = (title or "").strip().lower()
    norm_body = (body or "").strip().lower()
    blob = f"{exchange.lower()}\x1f{norm_title}\x1f{norm_body}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_notice(
    raw: RawNotice,
    *,
    source_name: str = "mock",
    collected_at: datetime | None = None,
) -> NormalizedNotice:
    """RawNotice → NormalizedNotice. 빈 title 등 비정상 입력은 ValueError."""
    title = (raw.title or "").strip()
    if not title:
        raise ValueError("notice title is empty")
    # 너무 긴 title 은 잘라 안전화 (DB Text 무제한이지만 UI/log 부담 회피).
    if len(title) > 512:
        title = title[:509] + "..."

    exchange = (raw.exchange or "").strip().lower()
    if not exchange:
        raise ValueError("notice exchange is empty")

    body = (raw.body or "").strip()

    # symbols: source 가 준 게 우선, 없으면 본문에서 추출.
    if raw.symbols:
        symbols = tuple(sorted({s.strip().upper() for s in raw.symbols if s and s.strip()}))
    else:
        symbols = extract_symbols(title, body)

    notice_type = classify_notice_type(title, body, raw.category or "")
    severity = compute_severity(notice_type, title, body)
    content_hash = compute_content_hash(exchange, title, body)

    # published_at fallback — None 이면 collected_at 그대로 둔다 (None 유지가 정직).
    return NormalizedNotice(
        exchange=exchange,
        title=title,
        notice_id=(raw.notice_id or None),
        url=(raw.url or ""),
        category=(raw.category or ""),
        notice_type=notice_type,
        severity=severity,
        body=body,
        symbols=symbols,
        published_at=raw.published_at,
        content_hash=content_hash,
        source_name=source_name,
        raw_payload=dict(raw.raw_payload or {}),
    )


# ── Collector ────────────────────────────────────────────────────


class NoticeCollector:
    """여러 NoticeSource 에서 공지를 받아 ExchangeNotice 로 영속화.

    중복 제거 규칙:
      1. (exchange, notice_id) — notice_id 있을 때. 기존 row 업데이트.
      2. (exchange, content_hash) — notice_id 없을 때. 기존 row 업데이트.

    direct_order_allowed 는 영구 False — collector 가 절대 True 로 쓰지 않는다.

    공지 이벤트가 직접 매수/매도로 이어지지 않도록 본 모듈은 broker/execution
    계층을 import 하지 않는다 (CLAUDE.md §2.3, §3.1).
    """

    def __init__(self, sources: dict[str, NoticeSource] | None = None):
        # 거래소 이름 → source. 기본은 MockNoticeSource 1개.
        if sources is None:
            sources = {"mock": MockNoticeSource("mock")}
        self.sources: dict[str, NoticeSource] = dict(sources)

    def add_source(self, name: str, source: NoticeSource) -> None:
        self.sources[name] = source

    def collect_once(
        self,
        session: Session,
        *,
        exchange: str | None = None,
        source_name: str | None = None,
        since: datetime | None = None,
        now: datetime | None = None,
    ) -> CollectResult:
        """단발 수집. exchange 미지정 시 등록된 모든 source 순회."""
        now = now or datetime.now(timezone.utc)
        if exchange is not None and source_name is None:
            source_name = exchange
        sources_to_run: list[tuple[str, NoticeSource]]
        if source_name is not None:
            if source_name not in self.sources:
                raise KeyError(f"unknown notice source: {source_name}")
            sources_to_run = [(source_name, self.sources[source_name])]
        else:
            sources_to_run = list(self.sources.items())

        fetched = 0
        inserted = 0
        updated = 0
        skipped = 0
        by_type: dict[str, int] = {t: 0 for t in NOTICE_TYPES}
        by_severity: dict[str, int] = {s: 0 for s in SEVERITIES}
        kept: list[ExchangeNotice] = []

        for sname, source in sources_to_run:
            try:
                ex_name = exchange if exchange is not None else getattr(source, "exchange", sname)
                raws = source.fetch_notices(ex_name, since=since)
            except Exception:
                # source 자체 실패는 무시 (다른 source 처리 계속).
                continue
            for raw in raws:
                fetched += 1
                try:
                    norm = normalize_notice(raw, source_name=sname, collected_at=now)
                except ValueError:
                    skipped += 1
                    continue
                row, was_new = self._upsert(session, norm, now=now)
                if was_new:
                    inserted += 1
                else:
                    updated += 1
                by_type[row.notice_type] = by_type.get(row.notice_type, 0) + 1
                by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
                kept.append(row)

        session.flush()
        return CollectResult(
            fetched=fetched,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            by_type=by_type,
            by_severity=by_severity,
            notices=tuple(kept),
        )

    @staticmethod
    def _upsert(
        session: Session,
        norm: NormalizedNotice,
        *,
        now: datetime,
    ) -> tuple[ExchangeNotice, bool]:
        """(exchange, notice_id) 우선, 부재 시 (exchange, content_hash) 로 dedup."""
        existing: ExchangeNotice | None = None
        if norm.notice_id:
            existing = session.execute(
                select(ExchangeNotice).where(
                    ExchangeNotice.exchange == norm.exchange,
                    ExchangeNotice.notice_id == norm.notice_id,
                )
            ).scalar_one_or_none()
        if existing is None:
            existing = session.execute(
                select(ExchangeNotice).where(
                    ExchangeNotice.exchange == norm.exchange,
                    ExchangeNotice.content_hash == norm.content_hash,
                )
            ).scalar_one_or_none()

        if existing is not None:
            existing.title = norm.title
            existing.url = norm.url or existing.url
            existing.category = norm.category or existing.category
            existing.notice_type = norm.notice_type
            existing.severity = norm.severity
            existing.body = norm.body or existing.body
            existing.symbols = list(norm.symbols)
            if norm.published_at is not None:
                existing.published_at = norm.published_at
            existing.content_hash = norm.content_hash
            existing.source_name = norm.source_name
            existing.raw_payload = norm.raw_payload
            existing.direct_order_allowed = False  # CLAUDE.md §2.3 — 영구 False
            existing.updated_at = now
            return existing, False

        row = ExchangeNotice(
            exchange=norm.exchange,
            notice_id=norm.notice_id,
            title=norm.title,
            url=norm.url,
            category=norm.category,
            notice_type=norm.notice_type,
            severity=norm.severity,
            body=norm.body,
            symbols=list(norm.symbols),
            published_at=norm.published_at,
            collected_at=now,
            content_hash=norm.content_hash,
            source_name=norm.source_name,
            direct_order_allowed=False,
            raw_payload=norm.raw_payload,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row, True


# ── Query helpers (read-only) ─────────────────────────────────────

def list_notices(
    session: Session,
    *,
    exchange: str | None = None,
    symbol: str | None = None,
    notice_type: str | None = None,
    severity: str | None = None,
    since_hours: int | None = None,
    since: datetime | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> list[ExchangeNotice]:
    """조건부 조회 — read-only."""
    now = now or datetime.now(timezone.utc)
    stmt = select(ExchangeNotice)
    if exchange is not None:
        stmt = stmt.where(ExchangeNotice.exchange == exchange.lower())
    if notice_type is not None:
        stmt = stmt.where(ExchangeNotice.notice_type == notice_type)
    if severity is not None:
        stmt = stmt.where(ExchangeNotice.severity == severity)
    if since is None and since_hours is not None and since_hours > 0:
        since = now - timedelta(hours=int(since_hours))
    if since is not None:
        stmt = stmt.where(ExchangeNotice.collected_at >= since)
    stmt = stmt.order_by(ExchangeNotice.collected_at.desc()).limit(int(limit))
    rows = list(session.execute(stmt).scalars().all())
    if symbol is not None:
        sym = symbol.strip().upper()
        rows = [r for r in rows if sym in (r.symbols or [])]
    return rows


def notice_to_dict(n: ExchangeNotice) -> dict[str, Any]:
    return {
        "id":           n.id,
        "exchange":     n.exchange,
        "notice_id":    n.notice_id,
        "title":        n.title,
        "url":          n.url,
        "category":     n.category,
        "notice_type":  n.notice_type,
        "severity":     n.severity,
        "symbols":      list(n.symbols or []),
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "collected_at": n.collected_at.isoformat() if n.collected_at else None,
        "source_name":  n.source_name,
        "direct_order_allowed": False,  # 영구 False
    }


__all__ = (
    "NOTICE_TYPES",
    "SEVERITIES",
    "RawNotice",
    "NormalizedNotice",
    "CollectResult",
    "NoticeSource",
    "MockNoticeSource",
    "NoticeCollector",
    "classify_notice_type",
    "compute_severity",
    "compute_content_hash",
    "extract_symbols",
    "normalize_notice",
    "list_notices",
    "notice_to_dict",
)
