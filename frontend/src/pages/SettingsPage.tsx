/** 체크리스트 #7: 환경설정 placeholder.
 *
 * 안전 원칙(CLAUDE.md §2.1.5): frontend 는 어떤 secret 도 저장하지 않는다.
 * API Key 입력란은 의도적으로 disabled 처리하고 안내 문구만 노출한다.
 * live mode 전환은 본 단계에서 동작하지 않는다.
 */
import ModeBadge from "../components/ModeBadge";

export default function SettingsPage() {
  return (
    <div className="page-stack">
      <h2 className="page-title">Settings</h2>

      <section className="section">
        <h3 className="section-title">Trading Mode</h3>
        <p>
          현재 모드: <ModeBadge mode="paper" />
        </p>
        <p className="muted">
          본 단계에서는 모드 전환을 UI 에서 트리거하지 않습니다. live 전환은
          governance 승인 + 환경변수 변경 + 별도 검증을 통과해야 합니다.
        </p>
      </section>

      <section className="section">
        <h3 className="section-title">API Key</h3>
        <label className="muted" htmlFor="api-key-disabled">
          거래소 API Key (저장 불가)
        </label>
        <input
          id="api-key-disabled"
          type="password"
          placeholder="이번 단계에서는 입력 불가"
          disabled
          aria-disabled="true"
          autoComplete="off"
          className="setting-input"
        />
        <p className="muted small">
          이번 단계에서는 API Key 를 저장하지 않습니다. secret 은 백엔드
          `.env` 에서만 관리합니다.
        </p>
      </section>

      <section className="section">
        <h3 className="section-title">알림 / 텔레메트리</h3>
        <p className="muted">추후 텔레그램/이메일 알림 설정이 들어갈 영역입니다.</p>
      </section>
    </div>
  );
}
