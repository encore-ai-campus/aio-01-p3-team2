import "./styles.css";

const $ = (selector, root = document) => root.querySelector(selector);
const app = $("#app");
let activeView = "create";
let currentRun = null;
let faultRun = null;
let policyState = null;
let policyPreview = null;
let policiesLoading = false;
let policyLoadError = "";
let logsState = null;
let logsError = "";
let loading = false;
let activeToast = null;
let activeToastTimer = null;

const views = [
  ["create", "기획안 만들기", "✦"],
  ["workflow", "진행 기록", "⌁"],
  ["policies", "복구 정책", "⛨"],
  ["fault", "복구 시연", "⚡"],
  ["logs", "운영 로그", "▤"],
];

function shell() {
  app.innerHTML = `
    <aside class="rail">
      <div class="brand-mark">EQ</div>
      <div class="brand-copy"><b>EVENTQUEST</b><span>LIVEOPS DESK</span></div>
      <div class="rail-divider"></div>
      <div class="nav-label">WORKSPACE</div>
      <nav>${views.map(([id, label, icon]) => `<button class="nav-item ${activeView === id ? "active" : ""}" data-view="${id}"><i>${icon}</i><span>${label}</span></button>`).join("")}</nav>
      <div class="rail-spacer"></div>
      <div class="profile"><div class="avatar">L</div><div><b>LiveOps</b><span>단일 사용자 권한</span></div><span class="online-dot"></span></div>
    </aside>
    <main class="main-shell">
      <header class="topbar"><div><span class="eyebrow">OPONG HEROES / PLANNING</span><h1 id="page-title"></h1></div><div class="top-meta"><span class="runtime-pill"><i></i> LOCAL RUNTIME</span><span class="version">CONSOLE 0.1</span></div></header>
      <section id="view-root" class="view-root"></section>
    </main>
    <div id="toast" class="toast" role="status"></div>`;
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => { activeView = button.dataset.view; render(); }));
}

function toast(message, error = false) {
  activeToast = { message, error };
  clearTimeout(activeToastTimer);
  showToast();
  activeToastTimer = setTimeout(() => {
    activeToast = null;
    showToast();
  }, 3200);
}

function showToast() {
  const node = $("#toast");
  if (!node) return;
  if (!activeToast) {
    node.textContent = "";
    node.className = "toast";
    return;
  }
  node.textContent = activeToast.message;
  node.className = `toast show ${activeToast.error ? "error" : ""}`;
}

function titleFor(view) {
  return views.find(([id]) => id === view)?.[1] ?? "운영";
}

function createView() {
  return `
    <div class="page-intro"><div><span class="eyebrow">NEW EVENT BRIEF</span><h2>게임 규칙 안에서 이벤트를 설계합니다.</h2><p>지원되는 조건은 근거를 붙이고, 지원 밖 기능은 개발 항목으로 분리해 보여줍니다.</p></div><div class="snapshot-card"><span>고정 게임 데이터</span><b>OPONG DATA 1.3.0</b><small>Runtime Capability와 동일한 snapshot</small></div></div>
    <form id="brief-form" class="brief-layout">
      <section class="panel form-panel"><div class="panel-heading"><div><span class="step">01</span><h3>이벤트 방향</h3></div><span class="required-note">필수 입력</span></div>
        <label>이벤트 목표<textarea name="objective" required maxlength="500" placeholder="예: 신규 유저가 첫 던전까지 자연스럽게 도달하도록 돕는다"></textarea></label>
        <div class="field-grid"><label>대상 유저<select name="segment"><option value="new_user">신규 유저</option><option value="returning_user">복귀 유저</option></select></label><label>이벤트 유형<select name="event_type"><option value="growth_progression">성장 지원</option><option value="dungeon_challenge">던전 도전</option><option value="special_dungeon">특별 던전</option></select></label></div>
        <div class="field-grid"><label>운영 방식<select name="operation_mode"><option value="finite">기간 한정</option><option value="ongoing">상시 운영</option></select></label><label>진행 기간 (일)<input name="duration" type="number" min="1" max="7" value="3" required /></label></div>
        <label>이벤트 장소<select name="venue"><option value="">기본 게임 공간</option><option value="moon_rift_v1">월광 균열 특별 던전</option></select><span class="field-hint">특별 던전을 선택하면 현재 지원되는 템플릿과 비교합니다.</span></label>
      </section>
      <section class="panel form-panel"><div class="panel-heading"><div><span class="step">02</span><h3>미션과 보상</h3></div><span class="link-badge">행동 → 보상 연결</span></div>
        <label>유저 행동<select name="action_type"><option value="stage_clear">스테이지 클리어</option><option value="dungeon_clear">던전 클리어</option><option value="level_reach">영웅 레벨 도달</option><option value="special_dungeon_floor_clear">특별 던전 층 클리어</option><option value="boss_defeat">보스 처치</option></select></label>
        <div class="field-grid"><label>달성 목표<input name="target" type="number" min="1" max="10000" value="3" required /></label><label>콘텐츠 ID<input name="content_id" value="stage_01" placeholder="예: stage_01" /></label></div>
        <div class="reward-link"><div class="link-line"><span>미션을 달성하면</span><b>이 보상 지급</b></div><div class="field-grid"><label>보상 아이템<select name="item_id"><option value="GOLD">골드</option><option value="GEM">보석</option><option value="MOON_SHARD">월광 파편</option><option value="POTION_SMALL">작은 회복 물약</option><option value="SKIN_STAR">별사탕 스킨</option></select></label><label>지급 수량<input name="quantity" type="number" min="1" value="20" required /></label></div></div>
        <label>기대 지표<input name="impact" value="이벤트 미션 참여율" required /><span class="field-hint">관측 근거가 없으면 결과에 unknown으로 표시됩니다.</span></label>
      </section>
      <div class="form-footer"><div class="privacy-note"><span>ⓘ</span><span>이 단계는 게임 데이터를 읽고 계획을 검증합니다.<br/>승인 전에는 게임 상태와 보상을 변경하지 않습니다.</span></div><button class="primary-button" type="submit" ${loading ? "disabled" : ""}>${loading ? "검증 중…" : "기획안 검증 시작"}<span>→</span></button></div>
    </form>
    ${currentRun ? resultCard() : ""}`;
}

function resultCard() {
  const plan = currentRun.plan;
  const decision = currentRun.decision ?? plan.decision;
  const runtimeEvent = currentRun.runtimeEvent ?? currentRun.runtime_event;
  const tone = plan.runtime_eligible ? "success" : plan.plan_status === "NEEDS_DATA" ? "warning" : "development";
  const label = plan.runtime_eligible ? "Runtime 지원 가능" : plan.plan_status === "NEEDS_DATA" ? "데이터 확인 필요" : plan.plan_status === "BLOCKED" ? "정책 차단" : "개발 항목 포함";
  const decisionActions = plan.runtime_eligible && !decision ? `<div class="decision-actions"><span>승인하면 이 버전이 가상 게임 Runtime에 등록 가능합니다.</span><button class="primary-button" data-approve-plan>승인 후 Runtime 등록 준비 →</button><button class="text-button" data-reject-plan>거절</button></div>` : decision?.approval_status === "APPROVED" && !runtimeEvent ? `<div class="decision-actions"><span>승인 기록이 저장되었습니다. 요청한 게임 데이터 버전을 다시 검증해 등록합니다.</span><button class="primary-button" data-apply-plan>가상 게임에 이벤트 등록 →</button></div>` : decision?.approval_status === "APPROVED" && runtimeEvent ? `<div class="notice-list success-note"><b>Runtime 이벤트 등록 완료</b><p>${escapeHtml(runtimeEvent.runtime_event_id)} · ${escapeHtml(runtimeEvent.status)}</p></div>` : decision?.approval_status === "REJECTED" ? `<span class="state-chip warning-chip">운영자가 거절한 버전</span>` : "";
  return `<section class="panel result-panel ${tone}"><div class="result-head"><div><span class="eyebrow">PLAN ${plan.plan_id} · V${plan.result_version}</span><h3>${label}</h3><p>${tone === "success" ? "게임 규칙과 Runtime 지원 범위에서 검증되었습니다." : "지원 여부와 필요한 후속 작업을 확인하세요."}</p></div><span class="status-orb">${plan.runtime_eligible ? "✓" : "!"}</span></div><div class="result-grid"><div><small>상태</small><b>${plan.plan_status}</b></div><div><small>데이터 기준</small><b>${plan.game_context.game_data_snapshot_id}</b></div><div><small>미션 / 보상</small><b>${plan.missions.length} / ${plan.rewards.length}</b></div><div><small>영향 근거</small><b>${plan.impact_assessment[0]?.status ?? "unknown"}</b></div></div>${plan.development_requirements.length ? `<div class="notice-list"><b>추가 개발 필요</b>${plan.development_requirements.map((item) => `<p>${escapeHtml(item.requested_behavior)} · ${escapeHtml(item.impact)}</p>`).join("")}</div>` : ""}${plan.validation_summary.findings.length ? `<div class="notice-list"><b>검증 결과</b>${plan.validation_summary.findings.map((item) => `<p>${escapeHtml(item.code)} · ${escapeHtml(item.message_safe)}</p>`).join("")}</div>` : ""}${decisionActions}<details><summary>근거와 진행 Trace 보기</summary><pre>${escapeHtml(JSON.stringify({ evidence_refs: plan.evidence_refs, agent_analysis: currentRun.agent_analysis, workflow: currentRun.trace, source: currentRun.execution_profile }, null, 2))}</pre></details></section>`;
}

async function decideCurrentPlan(action) {
  try {
    const plan = currentRun.plan;
    const response = await fetch("/api/v1/decisions", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ workflow_id: currentRun.workflow_id, plan_id: plan.plan_id, result_version: plan.result_version, action }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail?.message ?? "결정을 저장하지 못했습니다.");
    const workflowResponse = await fetch(`/api/v1/workflows/${encodeURIComponent(currentRun.workflow_id)}`);
    const persisted = await workflowResponse.json();
    if (!workflowResponse.ok) throw new Error("저장된 Workflow 기록을 다시 읽지 못했습니다.");
    currentRun = persisted;
    toast(action === "APPROVE" ? "승인 기록을 저장했습니다." : "기획안을 거절했습니다.");
  } catch (error) { toast(error.message, true); }
  render();
}

async function applyCurrentPlan() {
  try {
    const response = await fetch("/api/game/events/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: crypto.randomUUID(), task_id: currentRun.task_id, result_version: currentRun.plan.result_version }) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.message ?? body.detail?.message ?? "Runtime 등록에 실패했습니다.");
    const workflowResponse = await fetch(`/api/v1/workflows/${encodeURIComponent(currentRun.workflow_id)}`);
    const persisted = await workflowResponse.json();
    if (!workflowResponse.ok) throw new Error("등록 결과가 저장된 Workflow 기록에서 확인되지 않습니다.");
    currentRun = persisted;
    toast("승인된 이벤트를 가상 게임 Runtime에 등록했습니다.");
  } catch (error) { toast(error.message, true); }
  render();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

async function submitBrief(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const actionId = crypto.randomUUID();
  const rewardId = crypto.randomUUID();
  const brief = {
    objective: form.get("objective"), target_segments: [form.get("segment")], event_type: form.get("event_type"), operation_mode: form.get("operation_mode"),
    schedule: { timezone: "Asia/Seoul", duration_days: Number(form.get("duration")) },
    venue: form.get("venue") ? { capability_id: `venue.${form.get("venue")}`, template_id: form.get("venue") } : null,
    actions: [{ action_id: actionId, action_type: form.get("action_type"), content_scope: form.get("content_id") ? { content_ids: [form.get("content_id")] } : null, goal: { metric: form.get("action_type") === "level_reach" ? "hero_level" : "count", aggregation: form.get("action_type") === "level_reach" ? "max" : "count", operator: "gte", target: Number(form.get("target")) } }],
    rewards: [{ reward_id: rewardId, item_id: form.get("item_id"), quantity: Number(form.get("quantity")) }],
    mission_reward_links: [{ action_id: actionId, reward_id: rewardId }],
    impact_targets: [{ metric: form.get("impact"), unit: "users", measurement_time: "event_end", evidence_refs: [] }],
    source_game_version: "opong-data-1.3.0",
  };
  loading = true;
  render();
  try {
    const response = await fetch("/api/v1/event-briefs", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(brief) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail?.message ?? body.message ?? "요청을 처리하지 못했습니다.");
    currentRun = body;
    localStorage.setItem("eventquest.latestWorkflow", body.workflow_id);
    activeView = "workflow";
    toast(body.workflow_status === "SAFE_STOP" ? "안전 종료 기록을 저장했습니다." : "기획안 검증이 완료되었습니다.", body.workflow_status === "SAFE_STOP");
  } catch (error) {
    toast(error.message, true);
  } finally {
    loading = false;
    render();
  }
}

function workflowView() {
  if (!currentRun) return `<div class="empty-state"><div class="empty-icon">⌁</div><h2>진행 기록이 없습니다.</h2><p>기획안 만들기에서 Brief를 제출하면 실제 workflow event와 검증 결과가 표시됩니다.</p><button class="secondary-button" data-goto="create">기획안 만들기 →</button></div>`;
  const timeline = currentRun.trace.map((step, index) => `<div class="timeline-row"><span class="timeline-index">0${index + 1}</span><span class="timeline-marker ${step.status === "PASS" || step.status === "VALIDATED" ? "done" : ""}"></span><div><b>${escapeHtml(step.event.replaceAll(".", " · "))}</b><small>${escapeHtml(step.status)}</small></div></div>`).join("");
  const outcome = currentRun.plan ? resultCard() : `<div class="notice-list warning-note"><b>${escapeHtml(currentRun.error?.code ?? "SAFE_STOP")}</b><p>${escapeHtml(currentRun.error?.message ?? "안전 종료 기록을 저장했습니다.")}</p>${currentRun.llm_usage ? `<details><summary>사용량 계측 보기</summary><pre>${escapeHtml(JSON.stringify(currentRun.llm_usage, null, 2))}</pre></details>` : ""}</div>`;
  return `<div class="page-intro"><div><span class="eyebrow">WORKFLOW ${currentRun.workflow_id}</span><h2>실제 검증 경로와 결과</h2><p>단계는 실행된 이벤트에서 표시됩니다. 미리 정해진 총 단계 수를 보여주지 않습니다.</p></div><span class="state-chip">${currentRun.current_status ?? currentRun.workflow_status}</span></div><div class="workflow-grid"><section class="panel"><div class="panel-heading"><div><span class="step">TRACE</span><h3>진행 기록</h3></div><span class="link-badge">${currentRun.execution_profile}</span></div><div class="timeline">${timeline}</div><details><summary>실행 정보</summary><pre>${escapeHtml(JSON.stringify({ workflow_id: currentRun.workflow_id, recovery_policy_version: currentRun.recovery_policy_version, snapshot_digest: currentRun.snapshot_digest, created_at: currentRun.created_at }, null, 2))}</pre></details></section><section class="panel plan-mini">${outcome}</section></div>`;
}

function policiesView() {
  const versions = policyState?.versions ?? [];
  const active = versions.find((row) => row.status === "ACTIVE");
  const draft = [...versions].reverse().find((row) => row.status === "DRAFT");
  const routes = draft?.policy?.routes ?? active?.policy?.routes ?? {};
  const routeRows = Object.entries(routes).map(([code, route]) => `<div class="log-row policy-row"><span>${escapeHtml(code)}</span><select data-policy-action="${escapeHtml(code)}">${["FAIL_FAST", "RETRY_OPERATION", "RETRY_AGENT", "REVISE_PLAN", "SAFE_STOP"].map((action) => `<option ${route.action === action ? "selected" : ""}>${action}</option>`).join("")}</select><input data-policy-attempts="${escapeHtml(code)}" type="number" min="1" max="${policyState?.hard_max_attempts ?? 4}" value="${route.max_attempts}" /></div>`).join("");
  const audit = (policyState?.audit ?? []).map((item) => `<div class="log-row"><span>v${item.version}</span><span>${escapeHtml(item.action)} · ${escapeHtml(item.actor)}</span><span>${escapeHtml(item.created_at)}</span></div>`).join("");
  return `<div class="page-intro"><div><span class="eyebrow">RECOVERY POLICY</span><h2>복구 규칙을 통제합니다.</h2><p>코드로 고정된 보안·계약 안전조건은 수정할 수 없고, 허용된 오류 경로만 버전으로 관리합니다.</p></div><span class="state-chip">${active ? `ACTIVE v${active.version}` : "불러오는 중"}</span></div>
    ${!policyState ? `<section class="panel"><p>${policyLoadError ? escapeHtml(policyLoadError) : "정책 저장소를 불러오는 중입니다…"}</p>${policyLoadError ? `<button class="secondary-button" id="reload-policies">다시 시도</button>` : ""}</section>` : `<section class="panel policy-panel"><div class="panel-heading"><div><span class="step">VERSIONED POLICY</span><h3>오류별 대응</h3></div><span class="link-badge">최대 ${policyState.hard_max_attempts}회 · 코드 hard cap</span></div><div class="log-table"><div class="log-row header"><span>오류 코드</span><span>대응</span><span>최대 시도</span></div>${routeRows}</div><div class="policy-actions"><button class="secondary-button" id="save-policy-draft">초안 저장</button>${draft ? `<button class="secondary-button" id="validate-policy" data-version="${draft.version}">검증</button><button class="secondary-button" id="preview-policy" data-version="${draft.version}">변경 미리보기</button><button class="primary-button" id="activate-policy" data-version="${draft.version}">검증 후 활성화</button>` : ""}</div>${policyPreview ? `<div class="notice-list ${policyPreview.valid ? "success-note" : "warning-note"}"><b>미리보기 · Runtime/Workflow 미변경</b><p>${policyPreview.valid ? `${policyPreview.changed_routes.length}개 대응 경로 변경` : escapeHtml(policyPreview.errors.join(" · "))}</p>${policyPreview.changed_routes.map((item) => `<p>${escapeHtml(item.error_code)}: ${escapeHtml(JSON.stringify(item.before))} → ${escapeHtml(JSON.stringify(item.after))}</p>`).join("")}</div>` : ""}</section>
    <section class="panel"><div class="panel-heading"><div><span class="step">AUDIT</span><h3>정책 변경 기록</h3></div><span class="link-badge">불변 감사 기록</span></div><div class="log-table">${audit || `<p>감사 기록이 없습니다.</p>`}</div>${versions.filter((row) => row.status === "RETIRED").map((row) => `<button class="text-button" data-rollback="${row.version}">v${row.version}으로 rollback</button>`).join("")}</section>`}`;
}

async function loadPolicies() {
  if (policiesLoading) return;
  policiesLoading = true;
  try {
    const response = await fetch("/api/v1/recovery-policies");
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail?.message ?? "복구 정책을 불러오지 못했습니다.");
    policyState = body;
    policyLoadError = "";
  } catch (error) { policyLoadError = error.message; toast(error.message, true); }
  finally { policiesLoading = false; }
  render();
}

async function policyAction(path, body = null) {
  try {
    const response = await fetch(`/api/v1/recovery-policies${path}`, { method: body ? "POST" : "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, ...(body ? { body: JSON.stringify(body) } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail?.message ?? "정책 변경에 실패했습니다.");
    if (path.endsWith("/preview")) policyPreview = result;
    else if (path.endsWith("/validate")) toast(result.valid ? "정책 검증을 통과했습니다." : result.errors.join(" · "), !result.valid);
    else toast("복구 정책을 갱신했습니다.");
    policyState = await (await fetch("/api/v1/recovery-policies")).json();
  } catch (error) { toast(error.message, true); }
  render();
}

async function savePolicyDraft() {
  const source = [...(policyState?.versions ?? [])].reverse().find((row) => row.status === "DRAFT") ?? policyState?.versions?.find((row) => row.status === "ACTIVE");
  const routes = Object.fromEntries(Object.entries(source?.policy?.routes ?? {}).map(([code, route]) => [code, { action: $(`[data-policy-action="${code}"]`).value, max_attempts: Number($(`[data-policy-attempts="${code}"]`).value) }]));
  await policyAction("/drafts", { policy_id: "planning.default", routes });
}

function faultView() {
  return `<div class="page-intro"><div><span class="eyebrow">LOCAL FAULT LAB</span><h2>복구 흐름을 안전하게 시연합니다.</h2><p>사전 허용된 오류만 주입하고 실제 Task·정책·Trace 경로를 실행합니다. 게임 Runtime 데이터는 변경하지 않습니다.</p></div><span class="state-chip warning-chip">LOCAL ONLY</span></div><section class="panel form-panel fault-panel"><div class="panel-heading"><div><span class="step">SCENARIO</span><h3>시연 시나리오</h3></div><span class="link-badge">고정 allowlist</span></div><label>오류 상황<select id="fault-type"><option value="mcp_timeout_once">MCP 조회 1회 시간 초과 → 재시도</option><option value="mission_unreachable_once">미션 달성 불가 → 기획 수정·재검증</option><option value="retry_limit_exceeded">재시도 한도 소진 → 안전 종료</option></select></label><div class="fault-callout"><b>격리 범위</b><span>기획 Task, workflow trace만 생성합니다. 이벤트 등록·미션 진행·보상 지급 API는 호출하지 않습니다.</span></div><button class="primary-button" id="run-fault" ${loading ? "disabled" : ""}>${loading ? "시연 실행 중…" : "복구 시나리오 실행"}<span>⚡</span></button></section>${faultRun ? `<section class="panel result-panel ${faultRun.workflow_status === "FAILED" ? "warning" : "success"}"><div class="result-head"><div><span class="eyebrow">TASK ${faultRun.task_id}</span><h3>${faultRun.workflow_status === "FAILED" ? "안전 종료" : "후보 생성 완료"}</h3><p>${escapeHtml(faultRun.error?.message ?? "허용된 오류 복구 경로가 실행되었습니다.")}</p></div><span class="status-orb">${faultRun.workflow_status === "FAILED" ? "!" : "✓"}</span></div><div class="log-table"><div class="log-row header"><span>순서</span><span>행동</span><span>오류 코드 / 상태</span></div>${(faultRun.traces ?? []).map((row) => `<div class="log-row"><span>${row.sequence}</span><span>${escapeHtml(row.action)}</span><span>${escapeHtml(row.error_code ?? row.status)}</span></div>`).join("")}</div><details><summary>복구 상세 보기</summary><pre>${escapeHtml(JSON.stringify(faultRun.traces, null, 2))}</pre></details></section>` : ""}`;
}

async function runFaultScenario() {
  const selectedFault = $("#fault-type").value;
  loading = true;
  render();
  try {
    const payload = {
      actor_id: "local-operator",
      event_name: "격리 복구 시연",
      target_user: "new_user",
      duration: 2,
      objective: "허용된 오류 복구 경로와 안전 종료를 확인한다.",
      game_source: "opong",
      idempotency_key: crypto.randomUUID(),
      fault_type: selectedFault,
    };
    const response = await fetch("/api/test/events/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.message ?? body.detail?.message ?? "장애 시연을 실행하지 못했습니다.");
    const traceResponse = await fetch(`/api/tasks/${body.task_id}/trace`);
    const traceBody = await traceResponse.json();
    faultRun = { ...body, traces: traceBody.traces ?? [] };
    toast("허용된 복구 시나리오가 끝났습니다.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    loading = false;
    render();
  }
}

function logsView() {
  const rows = logsState ?? [];
  return `<div class="page-intro"><div><span class="eyebrow">OPERATIONS / AUDIT</span><h2>운영 기록</h2><p>DB에 저장된 최근 Workflow와 오류·승인 상태를 확인합니다.</p></div><button class="secondary-button" id="refresh-logs">새로고침</button></div>${logsState === null ? `<section class="panel"><p>저장 기록을 불러오는 중입니다…</p></section>` : logsError ? `<section class="panel"><p>${escapeHtml(logsError)}</p><button class="secondary-button" id="refresh-logs">다시 시도</button></section>` : rows.length ? `<section class="panel"><div class="log-table"><div class="log-row header"><span>Workflow</span><span>기획 목표</span><span>상태</span></div>${rows.map((row) => `<button class="log-row workflow-log-row" data-workflow-id="${escapeHtml(row.workflow_id)}"><span>${escapeHtml(row.workflow_id)}</span><span>${escapeHtml(row.brief.objective)}</span><span>${escapeHtml(row.current_status ?? row.decision?.approval_status ?? row.workflow_status)}</span></button>`).join("")}</div></section>${currentRun ? `<section class="panel"><div class="panel-heading"><div><span class="step">SELECTED TRACE</span><h3>선택한 실행 기록</h3></div><span class="link-badge">${currentRun.workflow_id}</span></div><div class="log-table"><div class="log-row header"><span>순서</span><span>이벤트</span><span>결과</span></div>${currentRun.trace.map((row) => `<div class="log-row"><span>${row.sequence}</span><span>${escapeHtml(row.event)}</span><span>${escapeHtml(row.status)}</span></div>`).join("")}</div><details><summary>세부 정보</summary><pre>${escapeHtml(JSON.stringify({snapshot_digest:currentRun.snapshot_digest,recovery_policy_version:currentRun.recovery_policy_version,trace:currentRun.trace},null,2))}</pre></details></section>` : ""}` : `<div class="empty-state"><div class="empty-icon">▤</div><h2>표시할 기록이 없습니다.</h2><p>기획 Workflow를 실행하면 저장 기록이 표시됩니다.</p></div>`}`;
}

async function loadLogs() {
  try {
    const response = await fetch("/api/v1/workflows?limit=50");
    const body = await response.json();
    if (!response.ok) throw new Error("운영 기록을 불러오지 못했습니다.");
    logsState = body;
    logsError = "";
  } catch (error) { toast(error.message, true); logsState = []; logsError = error.message; }
  render();
}

async function selectLoggedWorkflow(workflowId) {
  try {
    const response = await fetch(`/api/v1/workflows/${encodeURIComponent(workflowId)}`);
    const body = await response.json();
    if (!response.ok) throw new Error("선택한 Workflow를 읽지 못했습니다.");
    currentRun = body;
    localStorage.setItem("eventquest.latestWorkflow", body.workflow_id);
  } catch (error) { toast(error.message, true); }
  render();
}

async function restoreLatestWorkflow() {
  const workflowId = localStorage.getItem("eventquest.latestWorkflow");
  if (!workflowId || currentRun) return;
  try {
    const response = await fetch(`/api/v1/workflows/${encodeURIComponent(workflowId)}`);
    const body = await response.json();
    if (!response.ok) throw new Error("저장된 Workflow를 복원하지 못했습니다.");
    if (!currentRun && activeView === "create") {
      currentRun = body;
      activeView = "workflow";
      render();
    }
  } catch (error) { toast(error.message, true); }
}

function render() {
  shell();
  $("#page-title").textContent = titleFor(activeView);
  const content = { create: createView, workflow: workflowView, policies: policiesView, fault: faultView, logs: logsView }[activeView]();
  $("#view-root").innerHTML = content;
  $("#brief-form")?.addEventListener("submit", submitBrief);
  $("#run-fault")?.addEventListener("click", runFaultScenario);
  document.querySelector("[data-approve-plan]")?.addEventListener("click", () => decideCurrentPlan("APPROVE"));
  document.querySelector("[data-reject-plan]")?.addEventListener("click", () => decideCurrentPlan("REJECT"));
  document.querySelector("[data-apply-plan]")?.addEventListener("click", applyCurrentPlan);
  if (activeView === "policies" && !policyState && !policiesLoading && !policyLoadError) loadPolicies();
  if (activeView === "logs" && logsState === null) loadLogs();
  $("#save-policy-draft")?.addEventListener("click", savePolicyDraft);
  $("#reload-policies")?.addEventListener("click", () => { policyLoadError = ""; loadPolicies(); });
  $("#validate-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/validate`));
  $("#preview-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/preview`));
  $("#activate-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/activate`));
  document.querySelectorAll("[data-rollback]").forEach((button) => button.addEventListener("click", () => policyAction(`/${button.dataset.rollback}/rollback`)));
  $("#refresh-logs")?.addEventListener("click", () => { logsState = null; loadLogs(); });
  document.querySelectorAll("[data-workflow-id]").forEach((button) => button.addEventListener("click", () => selectLoggedWorkflow(button.dataset.workflowId)));
  document.querySelectorAll("[data-goto]").forEach((button) => button.addEventListener("click", () => { activeView = button.dataset.goto; render(); }));
  showToast();
}

render();
restoreLatestWorkflow();
