import "./styles.css";
import { renderWorkflowGraph } from "./workflow-progress.js";
import { mountFaultLab, renderFaultLab } from "./fault-lab-topology.js";

const $ = (selector, root = document) => root.querySelector(selector);
const app = $("#app");
const demoActorId = "liveops01";
let loggedIn = false;
let activeView = "create";
let currentRun = null;
let userStartedCreating = loggedIn;
let workflowPollTimer = null;
let workflowPollError = "";
const workflowFinishedStates = new Set(["WAITING_APPROVAL", "WAITING_REVIEW", "SAFE_STOP", "COMPLETED", "CANCELLED", "REJECTED", "REVISION_REQUESTED", "APPROVED", "APPLIED"]);
let runtimeProgress = null;
let runtimeProgressEventId = "";
let runtimeProgressLoading = false;
let runtimeProgressError = "";
let faultRun = null;
let policyState = null;
let policyPreview = null;
const policyActionKeyStorage = "eventquest.policyActionKeys";
const policyActionKeys = new Map(JSON.parse(sessionStorage.getItem(policyActionKeyStorage) ?? "[]"));
const pendingActionKeyStorage = "eventquest.pendingActionKeys";
const pendingActionKeys = new Map(JSON.parse(sessionStorage.getItem(pendingActionKeyStorage) ?? "[]"));
let policyFilter = "";
let policiesLoading = false;
let policyLoadError = "";
let logsState = null;
let logsError = "";
let logsFilter = "";
let planningCatalog = null;
let planningCatalogLoading = false;
let planningCatalogError = "";
let loading = false;
let briefError = "";
let briefDraft = null;
let activeToast = null;
let activeToastTimer = null;
let revisionOpen = false;
let revisionSubmitting = false;
let runtimeApplyPending = false;
const demoGameUrl = "http://127.0.0.1:5173/";

async function requestFingerprint(value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function pendingRequestKey(operation, fingerprint, extra = {}) {
  const previous = pendingActionKeys.get(operation);
  const request = previous?.fingerprint === fingerprint
    ? previous
    : { fingerprint, key: crypto.randomUUID(), ...extra };
  pendingActionKeys.set(operation, request);
  sessionStorage.setItem(pendingActionKeyStorage, JSON.stringify([...pendingActionKeys]));
  return request;
}

function clearPendingRequestKey(operation) {
  pendingActionKeys.delete(operation);
  sessionStorage.setItem(pendingActionKeyStorage, JSON.stringify([...pendingActionKeys]));
}

const views = [
  ["create", "기획안 만들기", "✦"],
  ["workflow", "진행 기록", "⌁"],
  ["policies", "복구 정책", "⛨"],
  ["fault", "복구 시연", "⚡"],
  ["logs", "운영 로그", "▤"],
];

function loginScreen() {
  app.innerHTML = `<main class="login-page"><div class="login-card">
    <section class="login-story" aria-label="서비스 소개"><img class="login-logo" src="/event_quest_ai_logo_mascot_v3.png" alt="EventQuest AI · 게임 속 퀘스트를 설계, 검토하는 AI" />
      <div class="login-story-copy"><p class="login-overline">OPONG HEROES · LIVE OPS</p><h1>게임 퀘스트 설계부터 검토까지<br><em>이제 AI에게 맡겨보세요</em></h1><p>게임 규칙 검증부터 오류 수정까지 AI가 완성해 드립니다.</p>
      <div class="login-features" aria-label="서비스 기능"><div><span class="login-feature-icon blue" aria-hidden="true">✦</span><strong>AI 기반<br>퀘스트 설계</strong></div><div><span class="login-feature-icon green" aria-hidden="true">◆</span><strong>자동 검수 및<br>리스크 감지</strong></div><div><span class="login-feature-icon violet" aria-hidden="true">▮</span><strong>AutoHealing<br>안정적인 서비스 운영</strong></div></div></div>
    </section>
    <section class="login-entry" aria-labelledby="login-title"><div class="login-service">OPONG HEROES <span>│ LIVE OPS</span></div><div class="login-entry-body"><span class="login-overline">LIVE OPS DESK</span><h2 id="login-title">로그인</h2><p class="login-welcome">운영 시스템에 접속하세요.</p>
      <form id="demo-login-form"><label for="demo-login-id">아이디</label><input id="demo-login-id" name="actor" value="${demoActorId}" readonly autocomplete="username" aria-describedby="demo-login-note" /><p id="demo-login-note">시연용 아이디가 미리 입력되어 있습니다.</p><button type="submit" class="login-submit">로그인 <span aria-hidden="true">→</span></button></form>
      <p class="login-disclaimer">내부 시연용 데모 로그인 · 아이디만 확인하며 비밀번호 인증은 제공하지 않습니다.</p></div></section>
  </div></main>`;
  $("#demo-login-form").addEventListener("submit", (event) => {
    event.preventDefault();
    loggedIn = true;
    activeView = "create";
    userStartedCreating = true;
    render();
  });
}

function logout() {
  loggedIn = false;
  clearTimeout(workflowPollTimer);
  localStorage.removeItem("eventquest.latestWorkflow");
  currentRun = null;
  briefDraft = null;
  runtimeProgress = null;
  runtimeProgressEventId = "";
  faultRun = null;
  policyState = null;
  logsState = null;
  activeView = "create";
  userStartedCreating = false;
  loginScreen();
}

function shell() {
  app.innerHTML = `
    <aside class="rail">
      <img class="rail-logo" src="/event_quest_ai_logo_mascot_v3.png" alt="EventQuest AI · 게임 속 퀘스트를 설계, 검토하는 AI" />
      <div class="rail-divider"></div>
      <div class="nav-label">WORKSPACE</div>
      <nav>${views.map(([id, label, icon]) => `<button class="nav-item ${activeView === id ? "active" : ""}" data-view="${id}" aria-label="${label}" title="${label}"><i aria-hidden="true">${icon}</i><span>${label}</span></button>`).join("")}</nav>
      <div class="rail-spacer"></div>
      <div class="profile"><div class="avatar">L</div><div><b>LiveOps</b><span>단일 사용자 권한</span></div><span class="online-dot"></span></div>
    </aside>
    <main class="main-shell">
      <header class="topbar"><div><span class="eyebrow">OPONG HEROES / PLANNING</span><h1 id="page-title"></h1></div><div class="top-meta"><span class="runtime-pill"><i></i> LOCAL RUNTIME</span><span class="version">CONSOLE 0.1</span><button class="logout-button" id="demo-logout" type="button">로그아웃</button></div></header>
      <section id="view-root" class="view-root"></section>
    </main>
    <div id="toast" class="toast" role="status"></div>`;
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => { activeView = button.dataset.view; if (activeView === "create") userStartedCreating = true; render(); }));
  $("#demo-logout").addEventListener("click", logout);
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

function workflowStatusLabel(status) {
  return ({
    RUNNING: "진행 중",
    RECEIVED: "요청 접수",
    COLLECTING_EVIDENCE: "게임 데이터 조회 중",
    PLANNING: "기획 설명 생성 중",
    VALIDATING: "게임 규칙 검증 중",
    CRITIC_REVIEW: "기획안 검토 중",
    CANCELLED: "진행 취소",
    WAITING_APPROVAL: "승인 대기",
    WAITING_REVIEW: "검토 대기",
    APPROVED: "승인 완료",
    REJECTED: "승인 거절",
    REVISION_REQUESTED: "수정 요청 완료",
    APPLIED: "가상 게임 등록 완료",
    COMPLETED: "기획 검증 완료",
    FAILED: "안전 종료",
    SAFE_STOP: "안전 중단",
  })[status] ?? "상태 확인 필요";
}

function planStatusLabel(plan) {
  if (
    plan.plan_status === "BLOCKED" &&
    plan.validation_summary?.findings?.some((item) => item.code === "MISSION_SEGMENT_LEVEL_UNREACHABLE")
  ) return "대상/미션 조건 수정 필요";
  return ({
    VALIDATED: "검증 완료",
    VALIDATED_WITH_DEVELOPMENT_ITEMS: "개발 항목 포함",
    NEEDS_DATA: "데이터 확인 필요",
    BLOCKED: "정책 차단",
  })[plan.plan_status] ?? "상태 확인 필요";
}

function traceEventLabel(event) {
  return ({
    "brief.accepted": "기획 요청 접수",
    "runtime.evidence_collected": "게임 규칙·데이터 조회",
    "handoff.accepted": "담당 단계 전달",
    "handoff.requested": "담당 단계 전달 요청",
    "handoff.rejected": "담당 단계 전달 거절",
    "supervisor.transition_decided": "Supervisor 상태 전이 결정",
    "supervisor.state_transition_decided": "Supervisor 상태 전이 결정",
    "planner.started": "Planner 기획 시작",
    "planner.output_validated": "기획 초안 검증",
    "capability.scope_checked": "게임 지원 범위 확인",
    "rule_balance.validation_completed": "게임 규칙·보상 검증",
    "critic.review_completed": "기획안 검토",
    "final_consistency.checked": "최종 일관성 확인",
    "approval.pending": "사용자 승인 대기",
    "approval.decision_recorded": "사용자 승인 결정",
    "runtime.event_registered": "가상 게임 이벤트 등록",
    "mcp.recovery_decided": "게임 데이터 조회 복구 처리",
    "mcp.recovery_resolved": "게임 데이터 대체 조회 성공",
    "agent.recovery_decided": "Supervisor 복구 결정",
    "agent.recovery_resolved": "Agent 복구 완료",
    "workflow.safe_stopped": "안전 중단 기록",
    "llm.usage_recorded": "AI 사용량 기록",
  })[event] ?? "추가 실행 단계";
}

function traceStatusLabel(status) {
  return ({
    SUCCESS: "완료",
    STARTED: "실행 시작",
    PASS: "통과",
    VALIDATED: "검증 완료",
    VALIDATED_WITH_DEVELOPMENT_ITEMS: "개발 항목 포함",
    SUPPORTED: "지원됨",
    NEEDS_DEVELOPMENT: "개발 필요",
    NEEDS_DATA: "자료 필요",
    UNSUPPORTED_BY_POLICY: "정책상 미지원",
    REVISE: "수정 필요",
    HUMAN_REVIEW: "사람 검토 필요",
    REVIEW: "사람 검토 필요",
    BLOCK: "차단",
    BLOCKED: "차단",
    WAITING_REVIEW: "검토 대기",
    WAITING_APPROVAL: "승인 대기",
    APPROVED: "승인 완료",
    APPLIED: "등록 완료",
    RETRY: "재시도",
    TOOL_FALLBACK: "대체 조회 시도",
    ERROR: "오류",
    FAILED: "실패",
    SAFE_STOP: "안전 중단",
  })[status] ?? "확인 필요";
}

function traceStatusTone(status) {
  if (["SUCCESS", "PASS", "VALIDATED", "APPROVED", "APPLIED"].includes(status)) return "done";
  if (["ERROR", "FAILED", "SAFE_STOP", "BLOCK", "BLOCKED", "UNSUPPORTED_BY_POLICY"].includes(status)) return "blocked";
  if (["RETRY", "TOOL_FALLBACK"].includes(status)) return "recovery";
  if (["NEEDS_DEVELOPMENT", "NEEDS_DATA", "VALIDATED_WITH_DEVELOPMENT_ITEMS", "REVISE", "REVIEW", "HUMAN_REVIEW", "WAITING_REVIEW", "WAITING_APPROVAL"].includes(status)) return "attention";
  return "";
}

function faultScenarioLabel(id) {
  return ({
    mcp_timeout_once: "일시적인 게임 데이터 조회 실패",
    mission_unreachable_once: "미션 조건 수정 및 재검증",
    retry_limit_exceeded: "복구 한도 소진",
  })[id] ?? "사전 등록 시연";
}

function faultScenarioOutcome(id, status) {
  if (id === "mcp_timeout_once") return "첫 조회가 시간 초과됐지만 한 번 재시도해 같은 게임 데이터 기준으로 검증을 마쳤습니다.";
  if (id === "mission_unreachable_once") return "검증 finding을 기획 단계로 전달해 수정하고 게임 데이터 재조회·전체 검증을 마쳤습니다.";
  if (id === "retry_limit_exceeded" && status === "FAILED") return "허용된 재시도 한도를 모두 사용해 안전하게 중단했습니다. 승인이나 게임 등록은 진행되지 않았습니다.";
  return "허용된 복구 시나리오를 실행했습니다.";
}

function faultTraceActionLabel(row) {
  if (row.action === "RUN_AGENT" && row.next_agent === "event_planner_agent") return "기획안 수정 전달";
  return ({
    create_task: "시연 작업 시작",
    get_event_planning_context: "게임 근거 조회",
    validate_plan: row.phase === "revalidation" ? "수정 후 규칙 재검증" : "규칙 검증",
    regenerate_plan: "기획안 수정",
    RETRY: "오류 복구 재시도",
    AGENT_RETRY: "에이전트 재시도",
    TOOL_RETRY: "게임 데이터 도구 재시도",
    TOOL_FALLBACK: "대체 조회 선택",
    SAFE_STOP: "안전 중단 처리",
    candidate_saved: "기획 후보 저장",
    safe_stop: "안전 중단 기록",
  })[row.action] ?? ({
    planning_context: "게임 근거 조회",
    validation: "기획 규칙 검증",
    revalidation: "수정 후 재검증",
    routing: "담당 단계 전달",
    recovery: "복구 정책 실행",
  })[row.phase] ?? "Workflow 진행 단계";
}

function faultTraceResultLabel(row) {
  if (row.action === "RETRY" || row.action.endsWith("_RETRY")) return "재시도";
  if (row.action === "TOOL_FALLBACK") return "대체 경로 사용";
  if (row.action === "SAFE_STOP" || row.action === "safe_stop") return "안전 종료";
  return traceStatusLabel(row.status);
}

function safeStopSummary(errorCode) {
  const summaries = {
    "ERR-DEP-MCP-001": ["게임 데이터 연결이 불안정해 검증을 중단했습니다.", "연결 상태를 확인한 뒤 새 기획 요청으로 다시 검증할 수 있습니다."],
    "ERR-DEP-MCP-002": ["게임 데이터 조회 응답을 확인하지 못해 검증을 중단했습니다.", "게임 데이터 도구 상태를 확인한 뒤 새 기획 요청으로 다시 검증할 수 있습니다."],
    "ERR-DEP-LLM-001": ["AI 응답이 늦어져 검증을 중단했습니다.", "모델 연결 상태를 확인한 뒤 새 기획 요청으로 다시 검증할 수 있습니다."],
    "ERR-DEP-LLM-002": ["AI 요청을 처리하지 못해 검증을 중단했습니다.", "모델 설정을 확인한 뒤 새 기획 요청으로 다시 검증할 수 있습니다."],
  };
  return summaries[errorCode] ?? ["검증을 완료하지 못해 안전하게 중단했습니다.", "원인을 확인한 뒤 새 기획 요청으로 다시 검증할 수 있습니다."];
}

function eventDungeonName(template) {
  return template?.template_id === "moon_rift_v1" ? "달빛 던전" : template?.name ?? "퀘스트 던전";
}

function createView() {
  if (!planningCatalog) {
    return `<div class="page-intro"><div><span class="eyebrow">NEW QUEST BRIEF</span><h2>퀘스트 던전 기획</h2><p>선택한 공간별 첫 클리어 미션과 보상을 구성합니다.</p></div></div><section class="panel">${planningCatalogLoading ? "퀘스트 구간과 보상 목록을 불러오는 중입니다…" : escapeHtml(planningCatalogError || "게임 카탈로그를 불러오지 못했습니다.")} ${planningCatalogError ? `<button class="secondary-button" id="reload-planning-catalog">다시 시도</button>` : ""}</section>`;
  }
  const template = planningCatalog.templates[0];
  const dungeonName = eventDungeonName(template);
  const spaceRows = template.spaces.map((space) => `<article class="space-mission-row" data-space-row data-space-id="${escapeHtml(space.id)}">
    <label class="space-mission-enable"><input type="checkbox" name="space_enabled" value="${escapeHtml(space.id)}" checked /><span><b>${escapeHtml(space.name)}</b><small>던전 구간 · 입장 Lv.${space.entry_level}${space.is_boss ? " · 보스" : ""}</small></span></label>
  </article>`).join("");
  return `
    <div class="page-intro"><div><span class="eyebrow">NEW QUEST BRIEF</span><h2>퀘스트 목표를 정하고 도전 구간을 선택합니다.</h2><p>AI 에이전트가 게임 데이터에 맞춰 제안합니다.</p></div><div class="snapshot-card"><span>게임 데이터 기준</span><b>${escapeHtml(planningCatalog.versions.game_data_snapshot_id)}</b><small>${escapeHtml(dungeonName)} 공간·보상 카탈로그</small></div></div>
    <form id="brief-form" class="brief-layout" novalidate>
      <section class="panel game-intro-panel" aria-labelledby="game-intro-title"><img src="/game-intro-play-loop-quest.png" alt="영웅이 전투하고 장비를 강화한 뒤 퀘스트 던전의 세 공간으로 향하는 그림" /><div class="game-intro-copy"><span class="eyebrow">GAME GUIDE</span><h3 id="game-intro-title">어떤 게임인가요?</h3><p>영웅을 키우며 스테이지와 던전을 도전하는 모바일 방치형 RPG입니다. 전투에서 얻은 자원으로 장비를 강화하고 다음 구역에 도전합니다.</p><div class="game-intro-flow"><span>전투로 자원 획득</span><span>장비 강화와 성장</span><span>퀘스트 던전 도전</span></div><p class="game-intro-task">여기서는 <b>‘${escapeHtml(dungeonName)}’</b>에서 열 공간을 선택합니다. 기획 에이전트가 각 공간의 첫 클리어 보상을 제안합니다.</p><small>이미지는 게임 진행을 설명하기 위한 시안입니다.</small></div></section>
      <section class="panel form-panel event-direction-panel"><div class="panel-heading"><div><span class="step">01</span><h3>퀘스트 기본 정보 입력</h3></div><span class="required-note">필수 입력</span></div>
        <label>기획 목표<textarea name="objective" required maxlength="500" placeholder="예: 신규 유저가 달빛 던전의 공간을 탐험하도록 돕는다"></textarea></label>
        <div class="field-grid"><label>대상 유저<select name="segment"><option value="new_user">신규 유저</option><option value="returning_user">복귀 유저</option></select></label><label>운영 방식<select name="operation_mode" id="operation-mode"><option value="finite">기간 한정</option><option value="ongoing">상시 운영</option></select></label></div>
        <label><span id="duration-label">퀘스트 기간 (일)</span><input name="duration" id="duration-days" type="number" min="1" max="7" value="3" required /><span class="field-hint" id="duration-hint">기간 한정 퀘스트의 실제 운영 기간입니다.</span></label>
        <div class="space-selection"><div class="panel-heading"><div><span class="step">02</span><h3>도전 구간 선택</h3></div></div>
        <p class="field-hint">‘달빛 던전’에서 퀘스트에 사용할 구간을 선택하세요.</p>
        <div class="space-mission-list">${spaceRows}</div>
        <p class="field-hint">공간별 첫 클리어 보상 아이템·수량은 기획 에이전트가 제안하며, 게임 규칙 검증 후 기획안에 표시됩니다.</p></div>
        <label>기대 지표<input name="impact" value="퀘스트 참여율" required /><span class="field-hint">관측 근거가 없으면 결과를 임의 추정하지 않고 ‘근거 부족 · 미산출’로 표시합니다.</span></label>
      </section>
      ${briefError ? `<div class="notice-list warning-note form-error" role="alert"><b>요청을 다시 확인하세요</b><p>${escapeHtml(briefError)}</p></div>` : ""}
      <div class="form-footer"><div class="privacy-note"><span>ⓘ</span><span>이 단계는 게임 데이터를 읽고 계획을 검증합니다.<br/>승인 전에는 게임 상태와 보상을 변경하지 않습니다.</span></div><button class="primary-button" type="submit" ${loading ? "disabled" : ""}>${loading ? "검증 중…" : "기획안 검증 시작"}<span>→</span></button></div>
    </form>`;
}

async function loadPlanningCatalog() {
  if (planningCatalogLoading || planningCatalog) return;
  planningCatalogLoading = true;
  planningCatalogError = "";
  render();
  try {
    const [templateResponse, itemResponse] = await Promise.all([
      fetch("/api/game/catalog/special_dungeon_templates"),
      fetch("/api/game/catalog/items"),
    ]);
    const [templateBody, itemBody] = await Promise.all([templateResponse.json(), itemResponse.json()]);
    if (!templateResponse.ok || !itemResponse.ok) throw new Error("게임 카탈로그를 불러오지 못했습니다.");
    const templates = templateBody.items ?? [];
    const items = itemBody.items ?? [];
    if (!templates.length || !templates[0].spaces?.length || !items.length) throw new Error("퀘스트 구간 또는 보상 카탈로그가 비어 있습니다.");
    planningCatalog = { templates, items, versions: templateBody.versions };
    planningCatalogError = "";
  } catch (error) {
    planningCatalogError = error.message;
  } finally {
    planningCatalogLoading = false;
    render();
  }
}

function resultCard() {
  const plan = currentRun.plan;
  const impactStatus = plan.impact_assessment[0]?.status ?? "unknown";
  const impactStatusLabel = {
    measured: "실측 근거 있음",
    calculated: "규칙 기반 계산",
    estimated: "근거 기반 추정",
    unknown: "근거 부족 · 미산출",
  }[impactStatus] ?? "확인 필요";
  const impactRows = plan.impact_assessment.map((assessment) => {
    const label = assessment.display_label ?? impactMetricLabel(assessment.metric);
    const detail = assessment.metric === "mission_accessibility" && assessment.status === "calculated"
      ? assessment.value === 1 ? "규칙 기반 · 시작 시점 접근 가능" : "규칙 기반 · 세그먼트 최고 레벨 초과"
      : assessment.metric === "mission_combat_feasibility" && assessment.status === "calculated"
      ? assessment.value === 1 ? "기본 성장 기준 · 규칙상 1회 전투 클리어 가능" : "기본 성장 기준 · 규칙상 1회 전투 클리어 불가"
      : assessment.value === null
      ? impactStatusLabelFor(assessment.status)
      : `${impactStatusLabelFor(assessment.status)} · ${new Intl.NumberFormat("ko-KR").format(assessment.value)} ${assessment.unit}`;
    return `<div class="impact-row"><b>${escapeHtml(label)}</b><span>${escapeHtml(detail)}</span></div>`;
  }).join("");
  const decision = currentRun.decision ?? plan.decision;
  const runtimeEvent = currentRun.runtimeEvent ?? currentRun.runtime_event;
  const runtimeTraceMissing = currentRun.runtime_event_trace_status === "MISSING";
  const unreachableContent = plan.validation_summary.findings.some((item) => item.code === "MISSION_SEGMENT_LEVEL_UNREACHABLE");
  const tone = plan.runtime_eligible ? "success" : plan.plan_status === "NEEDS_DATA" || unreachableContent ? "warning" : "development";
  const label = plan.runtime_eligible ? "Runtime 지원 가능" : plan.plan_status === "NEEDS_DATA" ? "데이터 확인 필요" : plan.plan_status === "BLOCKED" ? planStatusLabel(plan) : "개발 항목 포함";
  const resultExplanation = unreachableContent
    ? "대상 세그먼트의 최고 레벨보다 높은 콘텐츠입니다. 콘텐츠나 대상을 바꿔 다시 요청하세요."
    : tone === "success" ? "게임 규칙과 Runtime 지원 범위에서 검증되었습니다." : "지원 여부와 필요한 후속 작업을 확인하세요.";
  const decisionActions = currentRun.reconciliation_required ? `<div class="notice-list warning-note"><b>승인·게임 등록 기록 확인 필요</b><p>아래 기록 재조정으로 실제 반영 근거와 진행 상태를 연결하세요.</p></div>` : !decision ? `<div class="decision-actions"><span>수정 의견을 남기면 AI가 기획안을 다시 작성하고 검토합니다.</span>${plan.runtime_eligible ? '<button class="primary-button" data-approve-plan>승인</button>' : ''}<button class="secondary-button" data-open-revision>수정 요청</button><button class="secondary-button" data-reject-plan>거절</button>${revisionOpen ? '<form id="revision-form" class="revision-form"><label for="revision-comment">수정 의견</label><textarea id="revision-comment" maxlength="500" required placeholder="변경할 내용과 이유를 적어주세요."></textarea><button class="primary-button" type="submit" '+(revisionSubmitting ? 'disabled' : '')+'>수정 요청 보내기</button></form>' : ''}</div>` : decision?.approval_status === "APPROVED" && !runtimeEvent ? `<div class="decision-actions"><span>승인 기록이 저장되었습니다. 요청한 게임 데이터 버전을 다시 검증해 등록합니다.</span><button class="primary-button" data-apply-plan>가상 게임에 퀘스트 등록</button></div>` : decision?.approval_status === "APPROVED" && runtimeEvent && runtimeTraceMissing ? `<div class="notice-list warning-note"><b>Runtime 적용 완료 · Trace 복구 필요</b><p>${escapeHtml(runtimeEvent.runtime_event_id)} 이벤트는 저장됐지만 실행 기록 저장이 실패했습니다. 재시도해 Trace를 복구할 수 있습니다.</p><button class="secondary-button" data-apply-plan>Trace 저장 다시 시도 →</button></div>` : decision?.approval_status === "APPROVED" && runtimeEvent ? `<div class="notice-list success-note"><b>Runtime 이벤트 등록 완료</b><p>${escapeHtml(runtimeEvent.runtime_event_id)} · ${escapeHtml(runtimeEvent.status)}</p></div>` : decision?.approval_status === "REJECTED" ? `<span class="state-chip warning-chip">운영자가 거절한 버전</span>` : decision?.approval_status === "REVISION_REQUESTED" ? `<div class="notice-list"><b>수정 요청 완료</b><p>${escapeHtml(decision.approval_comment ?? "")}</p>${decision.revised_workflow_id ? `<button class="secondary-button" data-revised-workflow="${escapeHtml(decision.revised_workflow_id)}">수정된 기획안 보기</button>` : ""}</div>` : "";
  const findings = plan.validation_summary.findings;
  const spaceNames = new Map((planningCatalog?.templates ?? []).flatMap((item) => item.spaces.map((space) => [space.id, space.name])));
  const itemNames = new Map((planningCatalog?.items ?? []).map((item) => [item.item_id, item.name]));
  const supportLabels = { NEEDS_DEVELOPMENT: "추가 개발 필요", NEEDS_DATA: "데이터 확인 필요", UNSUPPORTED_BY_POLICY: "정책상 지원 불가" };
  const missionRewards = plan.missions.map((mission, index) => {
    const contentNames = (mission.content_scope?.content_ids ?? []).map((id) => spaceNames.get(id) ?? id);
    const action = { special_dungeon_space_clear: "공간 클리어", stage_clear: "스테이지 클리어", dungeon_clear: "던전 클리어" }[mission.action_type] ?? mission.action_type;
    const comparison = { gte: "이상", gt: "초과", eq: "달성", lte: "이하", lt: "미만" }[mission.goal?.operator] ?? mission.goal?.operator ?? "";
    const goal = `${action} · ${mission.goal?.target ?? "미정"}${mission.goal?.metric === "count" ? "회" : ` (${mission.goal?.metric ?? "목표 미정"})`} ${comparison}`;
    const rewardLines = plan.rewards.filter((reward) => reward.mission_id === mission.mission_id).map((reward) => {
      const support = supportLabels[reward.support_status];
      const policy = { once_per_user: "유저당 1회 지급", once_per_cycle: "주기당 1회 지급" }[reward.grant_policy] ?? reward.grant_policy ?? "지급 조건 확인 필요";
      return `<div class="plan-reward"><b>${escapeHtml(itemNames.get(reward.item_id) ?? reward.item_id)} × ${escapeHtml(reward.quantity)}</b><small>${escapeHtml(policy)}${reward.max_grants_per_user != null ? ` · 유저당 최대 ${escapeHtml(reward.max_grants_per_user)}회` : ""}</small>${reward.design_rationale ? `<small>선정 이유: ${escapeHtml(reward.design_rationale)}</small>` : ""}${support ? `<span class="plan-support-note">${escapeHtml(support)}</span>` : ""}</div>`;
    }).join("");
    return `<div class="space-plan-row plan-mission"><span class="plan-mission-number">${String(index + 1).padStart(2, "0")}</span><div class="plan-mission-copy"><b>${escapeHtml(contentNames.join(" · ") || action)}</b><small>${escapeHtml(goal)}</small>${supportLabels[mission.support_status] ? `<span class="plan-support-note">${escapeHtml(supportLabels[mission.support_status])}</span>` : ""}</div><div class="plan-rewards">${rewardLines || "연결된 보상 없음"}</div></div>`;
  }).join("");
  const findingLines = [...new Set(findings.flatMap((item) => [
    `${findingLabel(item.code)} · ${item.message_safe}`,
    item.suggested_action,
  ].filter(Boolean)))];
  const audience = (plan.audience?.segments ?? "").split(",").filter(Boolean).map((segment) => ({ new_user: "신규 유저", returning_user: "복귀 유저", all_user: "전체 유저" })[segment.trim()] ?? segment.trim()).join(" · ") || "대상 정보 없음";
  const operation = { finite: "기간 한정", ongoing: "상시 운영" }[plan.operation_mode] ?? "운영 방식 확인 필요";
  const duration = plan.operation_mode === "ongoing" ? "상시" : plan.schedule?.duration_days != null ? `${plan.schedule.duration_days}일` : "기간 미정";
  const venue = (planningCatalog?.templates ?? []).find((item) => item.template_id === plan.venue?.template_id || item.id === plan.venue?.template_id);
  const eventType = { special_dungeon: "특별 던전", growth_progression: "성장 이벤트", dungeon_challenge: "던전 도전" }[plan.event_type] ?? plan.event_type;
  const status = decision?.approval_status ? workflowStatusLabel(decision.approval_status) : planStatusLabel(plan);
  return `<section class="panel result-panel plan-document ${tone}" aria-label="생성된 이벤트 기획안">
    <header class="result-head plan-document-head"><div><span class="eyebrow">기획안 결과 · V${escapeHtml(plan.result_version)}</span><h3>생성된 이벤트 기획안</h3><p>이벤트의 운영 방향과 참여 조건, 보상을 한눈에 확인하세요.</p></div><span class="plan-document-status ${tone}" role="status">${escapeHtml(status)}</span></header>
    <section class="plan-document-section" aria-label="기획안 요약"><div class="plan-section-heading"><span>01</span><h4>기획안 요약</h4></div><p class="plan-objective">${escapeHtml(plan.objective || "기획 목표 정보가 없습니다.")}</p><dl class="plan-facts"><div><dt>대상 유저</dt><dd>${escapeHtml(audience)}</dd></div><div><dt>운영 방식</dt><dd>${escapeHtml(operation)}</dd></div><div><dt>운영 기간</dt><dd>${escapeHtml(duration)}</dd></div><div><dt>이벤트 유형</dt><dd>${escapeHtml(eventType)}</dd></div></dl></section>
    <section class="plan-document-section" aria-label="기획안 상세 내용"><div class="plan-section-heading"><span>02</span><h4>기획안 상세 내용</h4></div><div class="plan-content-heading"><b>${escapeHtml(venue ? eventDungeonName(venue) : "참여 미션과 보상")}</b><span>미션 ${plan.missions.length}개 · 보상 ${plan.rewards.length}개</span></div><div class="space-plan-summary plan-missions">${missionRewards}</div></section>
    <section class="plan-document-section plan-review" aria-label="검토 결과 및 보완 사항"><div class="plan-section-heading"><span>03</span><h4>검토 결과 및 보완 사항</h4></div><p class="plan-review-status"><b>${escapeHtml(label)}</b> · ${escapeHtml(resultExplanation)}</p><p class="plan-impact-summary">요청 영향: ${escapeHtml(impactStatusLabel)}</p>${plan.development_requirements.length ? `<div class="notice-list"><b>추가 개발 필요</b>${plan.development_requirements.map((item) => `<p>${escapeHtml(formatDevelopmentBehavior(item.requested_behavior))} · ${escapeHtml(item.impact)}</p>`).join("")}</div>` : ""}${findingLines.length ? `<div class="notice-list"><b>검증 결과</b>${findingLines.map((line) => `<p>${escapeHtml(line)}</p>`).join("")}</div>` : ""}${decisionActions}</section>
    <section class="plan-document-section plan-evidence" aria-label="상세 근거"><div class="plan-section-heading"><span>04</span><h4>상세 근거</h4></div><button class="secondary-button trace-log-button" type="button" data-goto="logs">근거와 진행 Trace 보기</button><details><summary>데이터 기준과 영향 근거 보기</summary><p class="plan-source">기획안 ${escapeHtml(plan.plan_id)} · 데이터 기준 ${escapeHtml(plan.game_context.game_data_snapshot_id)}</p><div class="impact-assessments"><b>영향 근거</b>${impactRows}</div></details></section>
  </section>`;
}

function impactMetricLabel(metric) {
  return ({
    participation: "퀘스트 참여율",
    mission_accessibility: "미션 콘텐츠 접근 가능성",
    mission_combat_feasibility: "기본 성장 전투 가능성",
  })[metric] ?? metric;
}

function formatDevelopmentBehavior(value) {
  const itemLabels = {
    SKIN_STAR_CANDY: "별사탕 모험가",
    SKIN_MUSHROOM: "숲버섯 여행자",
    SKIN_MOON_BUNNY: "달토끼 수호자",
    ITEM_001: "회복 포션",
    ITEM_002: "성장 상자",
  };
  const match = value.match(/^([A-Z0-9_]+) x(\d+)$/);
  if (!match || !itemLabels[match[1]]) return value;
  return `${itemLabels[match[1]]} × ${match[2]}`;
}

function findingLabel(code) {
  return ({
    ACTION_CONTENT_UNKNOWN: "요청한 게임 콘텐츠를 찾을 수 없음",
    ACTION_NOT_SUPPORTED: "요청한 행동은 현재 게임에서 지원하지 않음",
    REWARD_NOT_SUPPORTED: "요청한 보상은 현재 지급 범위에 없음",
    MISSION_SEGMENT_LEVEL_UNREACHABLE: "대상 유저가 이벤트 콘텐츠에 접근할 수 없음",
    REWARD_GRANT_POLICY_UNSUPPORTED: "요청한 보상 반복 지급 방식은 지원하지 않음",
  })[code] ?? "게임 규칙 확인이 필요함";
}

function impactStatusLabelFor(status) {
  return ({ measured: "실측", calculated: "규칙 기반 계산", estimated: "근거 기반 추정", unknown: "근거 부족 · 미산출" })[status] ?? "확인 필요";
}

function runtimeProgressPanel() {
  const runtimeEvent = currentRun?.runtimeEvent ?? currentRun?.runtime_event;
  const eventId = runtimeEvent?.runtime_event_id;
  if (!eventId) return "";
  if (runtimeProgressEventId !== eventId || runtimeProgressLoading) {
    return `<section class="panel runtime-progress"><div class="panel-heading"><div><span class="step">GAME RUNTIME</span><h3>이벤트 진행 현황</h3></div><span class="state-chip">${runtimeProgressLoading ? "불러오는 중" : "갱신 필요"}</span></div><p>${runtimeProgressError ? escapeHtml(runtimeProgressError) : "Runtime에 저장된 미션과 보상 기록을 불러옵니다."}</p><button class="secondary-button" data-refresh-runtime-progress ${runtimeProgressLoading ? "disabled" : ""}>다시 불러오기</button></section>`;
  }
  if (!runtimeProgress) {
    return `<section class="panel runtime-progress"><div class="panel-heading"><div><span class="step">GAME RUNTIME</span><h3>이벤트 진행 현황</h3></div><span class="state-chip warning-chip">확인 실패</span></div><p>${escapeHtml(runtimeProgressError || "Runtime 진행 상태를 읽지 못했습니다.")}</p><button class="secondary-button" data-refresh-runtime-progress>다시 불러오기</button></section>`;
  }
  const missions = runtimeProgress.missions ?? [];
  const rewards = runtimeProgress.rewards ?? [];
  const balances = runtimeProgress.user?.inventory ?? {};
  return `<section class="panel runtime-progress"><div class="panel-heading"><div><span class="step">GAME RUNTIME · ${escapeHtml(runtimeProgress.event?.versions?.game_data_snapshot_id ?? "")}</span><h3>이벤트 진행 현황</h3></div><span class="state-chip">${escapeHtml(runtimeProgress.event?.status ?? "UNKNOWN")}</span></div><p class="runtime-source">${escapeHtml(runtimeProgress.event?.event_name ?? "이벤트")} · source ${escapeHtml(runtimeProgress.event?.source_task_id ?? "")} · ${escapeHtml(eventId)}</p><div class="runtime-mission-list">${missions.map((mission) => `<div class="runtime-mission"><div><b>${escapeHtml(mission.name ?? mission.action_type ?? mission.mission_id)}</b><small>${mission.active_during_event ? "이벤트 기간 중" : `Day ${mission.day}`} · ${escapeHtml(mission.status ?? "active")}</small></div><strong>${mission.current_value} / ${mission.target}</strong></div>`).join("") || `<p>등록된 미션이 없습니다.</p>`}</div><div class="runtime-reward-summary"><b>지급된 보상</b>${rewards.length ? rewards.map((reward) => `<div><span>${escapeHtml(reward.item_name ?? reward.item_id)} × ${reward.quantity}</span><small>현재 보유 ${balances[reward.item_id] ?? 0}</small></div>`).join("") : `<p>아직 지급된 보상이 없습니다.</p>`}</div><button class="secondary-button" data-refresh-runtime-progress>진행 현황 새로고침</button></section>`;
}

async function loadRuntimeProgress(eventId) {
  if (runtimeProgressLoading) return;
  runtimeProgressEventId = eventId;
  runtimeProgressLoading = true;
  runtimeProgressError = "";
  render();
  try {
    const response = await fetch(`/api/game/events/${encodeURIComponent(eventId)}/progress`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.message ?? body.detail?.message ?? "Runtime 진행 현황을 불러오지 못했습니다.");
    runtimeProgress = body;
    runtimeProgressError = "";
  } catch (error) {
    runtimeProgress = null;
    runtimeProgressError = error.message;
  } finally {
    runtimeProgressLoading = false;
    render();
  }
}

async function decideCurrentPlan(action) {
  try {
    const plan = currentRun.plan;
    const decision = { workflow_id: currentRun.workflow_id, plan_id: plan.plan_id, result_version: plan.result_version, action };
    const fingerprint = await requestFingerprint(decision);
    const request = pendingRequestKey("plan-decision", fingerprint);
    const response = await fetch("/api/v1/decisions", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": request.key }, body: JSON.stringify(decision) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail?.message ?? "결정을 저장하지 못했습니다.");
    const workflowResponse = await fetch(`/api/v1/workflows/${encodeURIComponent(currentRun.workflow_id)}`);
    const persisted = await workflowResponse.json();
    if (!workflowResponse.ok) throw new Error("저장된 Workflow 기록을 다시 읽지 못했습니다.");
    currentRun = persisted;
    revisionOpen = false;
    clearPendingRequestKey("plan-decision");
    toast(action === "APPROVE" ? "승인 기록을 저장했습니다." : "기획안을 거절했습니다.");
  } catch (error) { toast(error.message, true); }
  render();
}

async function submitRevision(event) {
  event.preventDefault();
  if (revisionSubmitting || !currentRun?.plan) return;
  const comment = $("#revision-comment")?.value.trim();
  if (!comment) { toast("수정 의견을 입력해주세요.", true); return; }
  const plan = currentRun.plan;
  const requestBody = { workflow_id: currentRun.workflow_id, plan_id: plan.plan_id, result_version: plan.result_version, action: "REQUEST_REVISION", approval_comment: comment };
  revisionSubmitting = true;
  const submitButton = $("#revision-form button[type=submit]");
  if (submitButton) submitButton.disabled = true;
  try {
    const fingerprint = await requestFingerprint(requestBody);
    const request = pendingRequestKey("plan-revision", fingerprint);
    const response = await fetch("/api/v1/decisions", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": request.key }, body: JSON.stringify(requestBody) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail?.message ?? "수정 요청을 완료하지 못했습니다.");
    if (!body.workflow?.plan) throw new Error("수정된 기획안을 불러오지 못했습니다.");
    currentRun = body.workflow;
    localStorage.setItem("eventquest.latestWorkflow", currentRun.workflow_id);
    clearPendingRequestKey("plan-revision");
    revisionOpen = false;
    toast(`수정된 기획안 V${currentRun.plan.result_version}의 검토가 완료됐습니다.`);
  } catch (error) { toast(error.message, true); }
  finally { revisionSubmitting = false; render(); }
}

async function applyCurrentPlan(event) {
  if (runtimeApplyPending) return;
  const openGame = event.currentTarget.classList.contains("primary-button");
  const gameWindow = openGame ? window.open("about:blank", "_blank") : null;
  if (openGame && !gameWindow) {
    toast("새 창이 차단됐습니다. 이 사이트의 팝업을 허용한 뒤 다시 눌러주세요.", true);
    return;
  }
  if (gameWindow) gameWindow.opener = null;
  runtimeApplyPending = true;
  document.querySelector("[data-apply-plan]")?.setAttribute("disabled", "");
  let registered = false;
  try {
    const plan = currentRun.plan;
    const fingerprint = await requestFingerprint({ task_id: currentRun.task_id, plan_id: plan.plan_id, result_version: plan.result_version });
    const request = pendingRequestKey("runtime-apply", fingerprint);
    const requestId = request.key;
    currentRun = { ...currentRun, runtime_apply_request_id: requestId };
    const response = await fetch("/api/game/events/apply", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: requestId, task_id: currentRun.task_id, result_version: currentRun.plan.result_version }) });
    const body = await response.json();
    if (!response.ok) {
      const detail = body.detail;
      if (detail?.code === "ERR-OBS-TRACE-001" && detail.operation_status === "APPLIED") {
        registered = true;
        const workflowResponse = await fetch(`/api/v1/workflows/${encodeURIComponent(currentRun.workflow_id)}`);
        const persisted = await workflowResponse.json();
        if (workflowResponse.ok) {
          currentRun = { ...persisted, runtime_apply_request_id: requestId };
          if (persisted.runtime_event_trace_status === "MISSING") {
            toast("Runtime 적용은 완료됐습니다. Trace 복구를 다시 시도할 수 있습니다.", true);
          } else {
            toast("Runtime 적용 결과를 저장된 실행 기록에서 확인했습니다.");
          }
        } else {
          currentRun = {
            ...currentRun,
            current_status: "APPLIED",
            runtime_event: { runtime_event_id: detail.runtime_event_id, status: "applied" },
            runtime_event_trace_status: "MISSING",
          };
          toast("Runtime 적용은 완료됐지만 Trace 상태를 다시 읽지 못했습니다.", true);
        }
        return;
      }
      throw new Error(body.message ?? detail?.message ?? "Runtime 등록에 실패했습니다.");
    }
    registered = true;
    const workflowResponse = await fetch(`/api/v1/workflows/${encodeURIComponent(currentRun.workflow_id)}`);
    const persisted = await workflowResponse.json();
    if (!workflowResponse.ok) throw new Error("등록 결과가 저장된 Workflow 기록에서 확인되지 않습니다.");
    currentRun = persisted;
    clearPendingRequestKey("runtime-apply");
    toast("승인된 이벤트를 가상 게임 Runtime에 등록했습니다.");
  } catch (error) { toast(error.message, true); }
  finally {
    runtimeApplyPending = false;
    if (gameWindow && registered) gameWindow.location.replace(demoGameUrl);
    else gameWindow?.close();
    render();
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

async function submitBrief(event) {
  event.preventDefault();
  briefError = "";
  const formElement = event.currentTarget;
  formElement.querySelectorAll(".field-validation-error").forEach((node) => node.remove());
  formElement.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute("aria-invalid"));
  const selectedSpaceRows = [...formElement.querySelectorAll("[data-space-row]")].filter((row) => row.querySelector('[name="space_enabled"]')?.checked);
  if (!selectedSpaceRows.length) {
    briefError = "퀘스트에 사용할 도전 구간을 하나 이상 선택하세요.";
    render();
    return;
  }
  const invalidField = formElement.querySelector(":invalid");
  if (invalidField) {
    const message = document.createElement("span");
    message.className = "field-validation-error";
    message.setAttribute("role", "alert");
    message.textContent = invalidField.name === "objective" ? "퀘스트 목표를 입력하세요." : "입력값을 확인하세요.";
    invalidField.setAttribute("aria-invalid", "true");
    invalidField.insertAdjacentElement("afterend", message);
    invalidField.focus();
    return;
  }
  const form = new FormData(formElement);
  const selectedSpaces = selectedSpaceRows.map((row) => ({ space_id: row.dataset.spaceId }));
  const inputData = {
    ...Object.fromEntries([...form.entries()].filter(([key]) => key !== "space_enabled")),
    spaces: selectedSpaces,
  };
  const inputFingerprint = await requestFingerprint(inputData);
  const previous = pendingActionKeys.get("event-brief");
  const stableIds = previous?.fingerprint === inputFingerprint
    ? previous
    : {
      requestId: crypto.randomUUID(),
    };
  const pending = pendingRequestKey("event-brief", inputFingerprint, stableIds);
  const template = planningCatalog.templates[0];
  const actions = selectedSpaces.map((space) => ({
    action_type: "special_dungeon_space_clear",
    content_scope: { content_ids: [space.space_id] },
    goal: { metric: "count", aggregation: "count", operator: "gte", target: 1 },
  }));
  const brief = {
    objective: form.get("objective"), target_segments: [form.get("segment")], event_type: "special_dungeon", operation_mode: form.get("operation_mode"),
    schedule: { timezone: "Asia/Seoul", duration_days: Number(form.get("duration")) },
    venue: { capability_id: `venue.${template.template_id}`, template_id: template.template_id },
    actions,
    reward_selection: "planner",
    rewards: [],
    mission_reward_links: [],
    impact_targets: [{ metric: form.get("impact"), unit: "users", measurement_time: "event_end", evidence_refs: [] }],
    source_game_version: planningCatalog.versions.game_data_snapshot_id,
  };
  loading = true;
  render();
  try {
    const response = await fetch("/api/v1/event-briefs", { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": pending.key, "Prefer": "respond-async" }, body: JSON.stringify(brief) });
    const body = await response.json();
    if (!response.ok) {
      briefError = body.detail?.code === "ERR-SEC-INPUT-001"
        ? "보안 정책에 의해 요청이 차단됐습니다. 퀘스트 목표·대상·미션·보상에 집중해 다시 작성하세요. 차단된 요청은 저장하거나 실행하지 않았습니다."
        : body.detail?.message ?? body.message ?? "요청을 처리하지 못했습니다.";
      if (body.detail?.code === "ERR-SEC-INPUT-001") clearPendingRequestKey("event-brief");
      throw new Error(briefError);
    }
    clearPendingRequestKey("event-brief");
    currentRun = body;
    revisionOpen = false;
    localStorage.setItem("eventquest.latestWorkflow", body.workflow_id);
    activeView = "workflow";
    toast(response.status === 202 ? "요청을 접수했습니다. 진행 상태를 확인합니다." : body.workflow_status === "SAFE_STOP" ? "안전 종료 기록을 저장했습니다." : "기획안 검증이 완료되었습니다.", body.workflow_status === "SAFE_STOP");
    scheduleWorkflowPoll();
  } catch (error) {
    briefError ||= error.message;
    if (!briefError.startsWith("보안 정책에 의해 요청이 차단됐습니다.")) toast(error.message, true);
  } finally {
    loading = false;
    render();
  }
}

function scheduleWorkflowPoll() {
  clearTimeout(workflowPollTimer);
  if (!currentRun || workflowFinishedStates.has(currentRun.current_status ?? currentRun.workflow_status)) return;
  const id = currentRun.workflow_id;
  workflowPollTimer = setTimeout(async () => {
    try {
      const response = await fetch(`/api/v1/workflows/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error("진행 상태를 조회하지 못했습니다. 연결을 다시 확인하고 있습니다.");
      const result = await response.json();
      if (currentRun?.workflow_id !== id) return;
      currentRun = result;
      workflowPollError = "";
    } catch (error) {
      workflowPollError = error.message;
    }
    if (activeView === "workflow") render();
    scheduleWorkflowPoll();
  }, workflowPollError ? 3000 : 700);
}

function workflowView() {
  if (!currentRun) return `<div class="empty-state"><div class="empty-icon">⌁</div><h2>진행 기록이 없습니다.</h2><p>기획안 만들기에서 Brief를 제출하면 실제 workflow event와 검증 결과가 표시됩니다.</p><button class="secondary-button" data-goto="create">기획안 만들기 →</button></div>`;
  const graph = renderWorkflowGraph(currentRun, { escapeHtml, traceEventLabel, traceStatusLabel, traceStatusTone, planStatusLabel });
  const findings = currentRun.plan?.validation_summary?.findings ?? [];
  const findingCards = findings.length
    ? `<section class="review-card"><div class="review-card-heading"><span class="review-icon">✓</span><div><b>Plan 검증 결과</b><small>${findings.length}개 항목</small></div></div><ul>${findings.map((item) => `<li><b>${escapeHtml(findingLabel(item.code))}</b><span>${escapeHtml(item.message_safe ?? "")}</span>${item.suggested_action ? `<small>${escapeHtml(item.suggested_action)}</small>` : ""}</li>`).join("")}</ul></section>`
    : currentRun.plan ? `<section class="review-card review-card-quiet"><div class="review-card-heading"><span class="review-icon">✓</span><div><b>Plan 검증 결과</b><small>추가 검증 항목 없음</small></div></div></section>` : "";
  const critic = currentRun.agent_analysis?.critic;
  const criticCard = critic ? `<section class="review-card"><div class="review-card-heading"><span class="review-icon">◎</span><div><b>Critic 검토</b><small>${escapeHtml(critic.status ?? "기록됨")}</small></div></div><p>${escapeHtml(critic.review_note ?? critic.reason ?? "검토 상태만 기록되어 있습니다.")}</p></section>` : "";
  const reviewSummary = `${findingCards}${criticCard}${!currentRun.trace?.length && !findingCards && !criticCard ? `<div class="review-empty"><span>○</span><b>별도 검토·복구 기록이 없습니다</b><p>이 Workflow에서 확인 가능한 검증, 평가 또는 복구 정보가 없습니다.</p></div>` : ""}`;
  const [safeStopTitle, safeStopNextStep] = safeStopSummary(currentRun.error?.code);
  const outcome = currentRun.current_status === "CANCELLED" ? `<div class="notice-list"><b>진행을 취소했습니다</b><p>이후 결과는 반영하지 않습니다. 새 요청으로 다시 시작할 수 있습니다.</p></div>` : !workflowFinishedStates.has(currentRun.current_status ?? currentRun.workflow_status) ? `<div class="notice-list"><b>기획안을 처리하고 있습니다</b><p>마지막으로 저장된 작업과 복구 기록을 표시합니다.</p>${workflowPollError ? `<p role="status">${escapeHtml(workflowPollError)}</p>` : ""}</div>` : currentRun.plan ? resultCard() : `<div class="notice-list warning-note"><b>${escapeHtml(safeStopTitle)}</b><p>${escapeHtml(currentRun.error?.message ?? "안전 중단 기록을 저장했습니다.")}</p><p>${escapeHtml(safeStopNextStep)} 승인이나 게임 등록은 진행되지 않았습니다.</p><details><summary>오류 코드와 사용량 보기</summary><p>${escapeHtml(currentRun.error?.code ?? "SAFE_STOP")}</p>${currentRun.llm_usage ? `<pre>${escapeHtml(JSON.stringify(currentRun.llm_usage, null, 2))}</pre>` : ""}</details></div>`;
  const runtimePanel = runtimeProgressPanel();
  const controlPanel = currentRun.reconciliation_required
    ? `<div class="notice-list warning-note"><b>저장 상태 재조정이 필요합니다</b><p>실제 승인·등록 결과와 진행 상태가 다릅니다. 게임을 다시 등록하지 않고 근거로 기록을 연결합니다.</p><button class="secondary-button" id="reconcile-workflow">기록 재조정</button></div>`
    : !workflowFinishedStates.has(currentRun.current_status ?? currentRun.workflow_status)
      ? `<button class="secondary-button" id="cancel-workflow">진행 취소</button>` : "";

  return '<div class="page-intro"><div><span class="eyebrow">WORKFLOW ' + escapeHtml(currentRun.workflow_id) + '</span><h2>실제 검증 경로와 결과</h2><p>주 흐름에서 갈라지는 검증·평가와 조정·복구 경로를 함께 확인하세요.</p></div><span class="state-chip">' + workflowStatusLabel(currentRun.current_status ?? currentRun.workflow_status) + '</span></div><div class="workflow-grid workflow-progress"><section class="panel workflow-board"><div class="panel-heading"><div><span class="step">WORKFLOW MAP</span><h3>진행 기록</h3></div><span class="link-badge">실행 이력</span></div><div class="route-legend"><span class="route-legend-process">실행 순서 · 분기 · 합류</span><span class="route-legend-control">Supervisor 조정·복구</span><span class="route-legend-spine">주 흐름 기준선</span></div>' + graph + '<details><summary>실행 정보와 전체 Trace</summary><pre>' + escapeHtml(JSON.stringify({ workflow_id: currentRun.workflow_id, execution_profile: currentRun.execution_profile, recovery_policy_version: currentRun.recovery_policy_version, snapshot_digest: currentRun.snapshot_digest, created_at: currentRun.created_at, trace: currentRun.trace }, null, 2)) + '</pre></details><div class="workflow-review-summary">' + reviewSummary + '</div></section><section class="panel plan-mini ' + (runtimePanel ? "has-runtime-progress" : "") + '">' + outcome + controlPanel + runtimePanel + '</section></div>';
}

function compatiblePolicyRoutes(version) {
  const routes={...(policyState?.suggested_draft?.routes ?? {}),...(version?.policy?.routes ?? {})};
  for(const [code,source] of Object.entries(version?.compatibility_mappings ?? {})) {
    if(!version.policy.routes[code] && version.policy.routes[source]) routes[code]={...version.policy.routes[source]};
  }
  return routes;
}

function policiesView() {
  const versions = policyState?.versions ?? [];
  const active = versions.find((row) => row.status === "ACTIVE");
  const draft = [...versions].reverse().find((row) => row.status === "DRAFT");
  const routes = compatiblePolicyRoutes(draft ?? active);
  const actionLabels = {
    FAIL_FAST: "즉시 종료",
    RETRY_OPERATION: "작업 재시도",
    RETRY_AGENT: "Agent 재시도",
    REVISE_PLAN: "기획 수정 후 재검증",
    SAFE_STOP: "안전 종료",
  };
  const normalizedPolicyFilter = policyFilter.trim().toLocaleLowerCase();
  const routeEntries = Object.entries(routes);
  const visibleRouteEntries = routeEntries.filter(([code, route]) => {
    const definition = policyState?.error_catalog?.[code];
    const searchText = [code, definition?.safe_message, definition?.category, definition?.location,
      actionLabels[route.action] ?? route.action].join(" ").toLocaleLowerCase();
    return !normalizedPolicyFilter || searchText.includes(normalizedPolicyFilter);
  });
  const routeRows = visibleRouteEntries.map(([code, route]) => {
    const definition = policyState?.error_catalog?.[code];
    const explanation = definition?.safe_message ?? "분류 설명을 확인할 수 없습니다.";
    return `<div class="log-row policy-row" data-policy-row><span class="policy-error-label"><b>${escapeHtml(explanation)}</b><small>${escapeHtml(code)} · ${escapeHtml(definition?.category ?? "UNKNOWN")}/${escapeHtml(definition?.location ?? "UNKNOWN")}</small></span><select aria-label="${escapeHtml(explanation)} 대응" data-policy-action="${escapeHtml(code)}">${Object.entries(actionLabels).map(([action, label]) => `<option value="${action}" ${route.action === action ? "selected" : ""}>${label}</option>`).join("")}</select><input aria-label="${escapeHtml(explanation)} 최대 시도 횟수" data-policy-attempts="${escapeHtml(code)}" type="number" min="1" max="${policyState?.hard_max_attempts ?? 4}" value="${route.max_attempts}" /></div>`;
  }).join("");
  const auditEntries = policyState?.audit ?? [];
  const auditRow = (item) => `<div class="log-row"><span>v${item.version}</span><span>${escapeHtml(item.action)} · ${escapeHtml(item.actor)}</span><span>${escapeHtml(item.created_at)}</span></div>`;
  const audit = auditEntries.slice(0, 8).map(auditRow).join("");
  const olderAudit = auditEntries.slice(8).map(auditRow).join("");
  const retiredVersions = versions.filter((row) => row.status === "RETIRED");
  const compatibilityNotice = active?.compatibility_mappings?.["ERR-DEP-MCP-003"] ? `<div class="notice-list"><b>기존 정책의 게임 규칙 조회 대응을 유지합니다</b><p>ERR-DEP-MCP-003은 기존 ERR-DEP-MCP-002의 대응을 상속합니다. 기존 버전은 보존되며, 새 초안을 활성화하면 두 코드를 따로 관리할 수 있습니다.</p></div>` : "";
  const contractNotice = active?.current_contract_errors?.length
    ? `<div class="notice-list warning-note"><b>활성 정책 보충이 필요합니다</b><p>현재 코드에 필요한 대응 경로가 없습니다. 아래 초안에는 제안 경로가 포함됩니다. 초안 저장 → 검증 → 미리보기 → 활성화를 진행하세요.</p></div>` : "";
  return `<div class="page-intro"><div><span class="eyebrow">RECOVERY POLICY</span><h2>복구 규칙을 통제합니다.</h2><p>코드로 고정된 보안·계약 안전조건은 수정할 수 없고, 허용된 오류 경로만 버전으로 관리합니다.</p></div><span class="state-chip">${active ? `ACTIVE v${active.version}` : "불러오는 중"}</span></div>
    ${!policyState ? `<section class="panel"><p>${policyLoadError ? escapeHtml(policyLoadError) : "정책 저장소를 불러오는 중입니다…"}</p>${policyLoadError ? `<button class="secondary-button" id="reload-policies">다시 시도</button>` : ""}</section>` : `${contractNotice}${compatibilityNotice}<section class="panel policy-panel"><div class="panel-heading"><div><span class="step">VERSIONED POLICY</span><h3>오류별 대응</h3></div><span class="link-badge">최대 ${policyState.hard_max_attempts}회 · 코드 hard cap</span></div><div class="log-search policy-search"><label for="policy-search">오류 정책 찾기</label><div><input id="policy-search" type="search" value="${escapeHtml(policyFilter)}" placeholder="오류 설명, 코드, 분류, 대응" /><span id="policy-search-count" role="status" aria-live="polite">${visibleRouteEntries.length}개 / ${routeEntries.length}개 표시</span></div></div><div class="log-table policy-route-rows"><div class="log-row header policy-row policy-header"><span>오류 원인 · 코드</span><span>대응</span><span>최대 시도</span></div>${routeRows}<div class="empty-state policy-filter-empty" ${visibleRouteEntries.length ? "hidden" : ""}><h2>일치하는 오류 정책이 없습니다.</h2><p>오류 설명, 코드, 분류 또는 대응 이름을 확인하세요.</p></div></div><div class="policy-actions"><button class="secondary-button" id="save-policy-draft">초안 저장</button>${draft ? `<button class="secondary-button" id="validate-policy" data-version="${draft.version}">검증</button><button class="secondary-button" id="preview-policy" data-version="${draft.version}">변경 미리보기</button><button class="primary-button" id="activate-policy" data-version="${draft.version}">검증 후 활성화</button>` : ""}</div>${policyPreview ? `<div class="notice-list ${policyPreview.valid ? "success-note" : "warning-note"}"><b>미리보기 · Runtime/Workflow 미변경</b><p>${policyPreview.valid ? `${policyPreview.changed_routes.length}개 대응 경로 변경` : escapeHtml(policyPreview.errors.join(" · "))}</p>${policyPreview.changed_routes.map((item) => `<p>${escapeHtml(item.error_code)}: ${escapeHtml(JSON.stringify(item.before))} → ${escapeHtml(JSON.stringify(item.after))}</p>`).join("")}</div>` : ""}</section>
    <section class="panel"><div class="panel-heading"><div><span class="step">AUDIT</span><h3>정책 변경 기록</h3></div><span class="link-badge">불변 감사 기록</span></div><div class="log-table policy-audit-list">${audit || `<p>감사 기록이 없습니다.</p>`}</div>${olderAudit ? `<details class="policy-history"><summary>이전 변경 기록 ${auditEntries.length - 8}건</summary><div class="log-table">${olderAudit}</div></details>` : ""}${retiredVersions.length ? `<details class="policy-history"><summary>이전 정책 버전으로 되돌리기 · ${retiredVersions.length}</summary><div class="policy-rollback-list">${retiredVersions.map((row) => `<button class="text-button" data-rollback="${row.version}">v${row.version}으로 rollback</button>`).join("")}</div></details>` : ""}</section>`}`;
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
    const requestBody = body ? JSON.stringify(body) : null;
    const fingerprint = requestBody ?? "";
    const previous = policyActionKeys.get(path);
    const idempotencyKey = previous?.fingerprint === fingerprint ? previous.key : crypto.randomUUID();
    policyActionKeys.set(path, { fingerprint, key: idempotencyKey });
    sessionStorage.setItem(policyActionKeyStorage, JSON.stringify([...policyActionKeys]));
    const response = await fetch(`/api/v1/recovery-policies${path}`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey }, ...(requestBody ? { body: requestBody } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail?.message ?? "정책 변경에 실패했습니다.");
    policyActionKeys.delete(path);
    sessionStorage.setItem(policyActionKeyStorage, JSON.stringify([...policyActionKeys]));
    if (path.endsWith("/preview")) policyPreview = result;
    else if (path.endsWith("/validate")) toast(result.valid ? "정책 검증을 통과했습니다." : result.errors.join(" · "), !result.valid);
    else toast("복구 정책을 갱신했습니다.");
    policyState = await (await fetch("/api/v1/recovery-policies")).json();
  } catch (error) { toast(error.message, true); }
  render();
}

async function savePolicyDraft() {
  const source = [...(policyState?.versions ?? [])].reverse().find((row) => row.status === "DRAFT") ?? policyState?.versions?.find((row) => row.status === "ACTIVE");
  const routes = Object.fromEntries(Object.entries(compatiblePolicyRoutes(source)).map(([code, route]) => [code, { action: $(`[data-policy-action="${code}"]`).value, max_attempts: Number($(`[data-policy-attempts="${code}"]`).value) }]));
  await policyAction("/drafts", { policy_id: "planning.default", routes });
}

function faultView() {
  return renderFaultLab();
}

function legacyFaultView() {
  const selectedFault = faultRun?.fault_type ?? "mcp_timeout_once";
  const retryCount = (faultRun?.traces ?? []).filter((row) => row.action === "RETRY" || row.action.endsWith("_RETRY")).length;
  const scenarios = [
    ["mcp_timeout_once", "MCP 조회 1회 시간 초과 → 재시도"],
    ["mission_unreachable_once", "미션 달성 불가 → 기획 수정·재검증"],
    ["retry_limit_exceeded", "재시도 한도 소진 → 안전 종료"],
  ];
  const scenarioOptions = scenarios.map(([id, label]) => `<option value="${id}" ${selectedFault === id ? "selected" : ""}>${label}</option>`).join("");
  const result = faultRun ? `<section class="panel result-panel fault-result ${faultRun.workflow_status === "FAILED" ? "warning" : "success"}">
    <div class="result-head"><div><span class="eyebrow">TASK ${escapeHtml(faultRun.task_id)}</span><h3>${faultRun.workflow_status === "FAILED" ? "안전 종료" : "복구 성공"}</h3><p>${escapeHtml(faultScenarioOutcome(faultRun.fault_type, faultRun.workflow_status))}</p></div><span class="status-orb">${faultRun.workflow_status === "FAILED" ? "!" : "✓"}</span></div>
    <div class="result-grid fault-summary"><div><small>시연 시나리오</small><b>${escapeHtml(faultScenarioLabel(faultRun.fault_type))}</b></div><div><small>재시도</small><b>${retryCount}회</b></div><div><small>기획 수정</small><b>${faultRun.revision_count ?? 0}회</b></div><div><small>최종 상태</small><b>${escapeHtml(workflowStatusLabel(faultRun.workflow_status))}</b></div></div>
    <div class="log-table fault-trace-list"><div class="log-row header"><span>순서</span><span>복구 단계</span><span>결과</span></div>${(faultRun.traces ?? []).map((row) => `<div class="log-row"><span>${row.sequence}</span><span>${escapeHtml(faultTraceActionLabel(row))}</span><span>${escapeHtml(faultTraceResultLabel(row))}</span></div>`).join("")}</div>
    <details><summary>Trace 원문과 오류 코드 보기</summary><pre>${escapeHtml(JSON.stringify(faultRun.traces, null, 2))}</pre></details>
  </section>` : "";
  return `<div class="page-intro"><div><span class="eyebrow">LOCAL FAULT LAB</span><h2>복구 흐름을 안전하게 시연합니다.</h2><p>사전 허용된 오류만 주입하고 실제 Task·정책·Trace 경로를 실행합니다. 게임 Runtime 데이터는 변경하지 않습니다.</p></div><span class="state-chip warning-chip">LOCAL ONLY</span></div><section class="panel form-panel fault-panel"><div class="panel-heading"><div><span class="step">SCENARIO</span><h3>시연 시나리오</h3></div><span class="link-badge">고정 allowlist</span></div><label>오류 상황<select id="fault-type">${scenarioOptions}</select></label><div class="fault-callout"><b>격리 범위</b><span>기획 Task, workflow trace만 생성합니다. 이벤트 등록·미션 진행·보상 지급 API는 호출하지 않습니다.</span></div><button class="primary-button" id="run-fault" ${loading ? "disabled" : ""}>${loading ? "시연 실행 중…" : "복구 시나리오 실행"}<span>⚡</span></button></section>${result}`;
}

async function runFaultScenario() {
  const selectedFault = $("#fault-type").value;
  loading = true;
  render();
  try {
    const payload = {
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
    faultRun = { ...body, fault_type: selectedFault, traces: traceBody.traces ?? [] };
    toast("허용된 복구 시나리오가 끝났습니다.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    loading = false;
    render();
  }
}

function logsView() {
  const rows = [...(logsState ?? [])].sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  const query = logsFilter.trim().toLocaleLowerCase();
  const visibleRows = (query ? rows.filter((row) => [row.workflow_id, row.task_id, row.brief?.objective].some((value) => value?.toLocaleLowerCase().includes(query))) : rows).slice(0, 10);
  const logList = visibleRows.length
    ? `<div class="log-table"><div class="log-row header"><span>Workflow</span><span>기획 목표</span><span>상태</span></div>${visibleRows.map((row) => `<button class="log-row workflow-log-row" data-workflow-id="${escapeHtml(row.workflow_id)}"><span>${escapeHtml(row.workflow_id)}</span><span>${escapeHtml(row.brief.objective)}</span><span>${workflowStatusLabel(row.current_status ?? row.decision?.approval_status ?? row.workflow_status)}</span></button>`).join("")}</div>`
    : `<div class="empty-state"><div class="empty-icon">▤</div><h2>${query ? "최근 기록에서 검색 결과가 없습니다." : "표시할 기록이 없습니다."}</h2><p>${query ? "Workflow ID, Task ID 또는 기획 목표를 확인해 다시 검색하세요." : "기획 Workflow를 실행하면 저장 기록이 표시됩니다."}</p></div>`;
  const selectedTrace = currentRun ? `<section class="panel"><div class="panel-heading"><div><span class="step">SELECTED TRACE</span><h3>선택한 TRACE 기록</h3></div><span class="link-badge">${currentRun.workflow_id}</span></div><div class="log-table"><div class="log-row header"><span>순서</span><span>이벤트</span><span>결과</span></div>${currentRun.trace.map((row) => `<div class="log-row"><span>${row.sequence}</span><span>${escapeHtml(traceEventLabel(row.event))}</span><span>${escapeHtml(traceStatusLabel(row.status))}</span></div>`).join("")}</div>${supervisorDecisionDetails(currentRun.trace)}<details><summary>원문 Trace와 실행 정보</summary><pre>${escapeHtml(JSON.stringify({snapshot_digest:currentRun.snapshot_digest,recovery_policy_version:currentRun.recovery_policy_version,trace:currentRun.trace},null,2))}</pre></details></section>` : "";
  return `<div class="page-intro"><div><span class="eyebrow">OPERATIONS / AUDIT</span><h2>운영 기록</h2><p>최근 50건에서 Workflow ID, Task ID 또는 기획 목표를 찾습니다.</p></div><button class="secondary-button" id="refresh-logs">새로고침</button></div>${selectedTrace}${logsState === null ? `<section class="panel"><p>저장 기록을 불러오는 중입니다…</p></section>` : logsError ? `<section class="panel"><p>${escapeHtml(logsError)}</p><button class="secondary-button" id="refresh-logs">다시 시도</button></section>` : `<section class="panel"><form id="log-search-form" class="log-search"><h3 class="log-search-title"><label for="workflow-search">이전 기획안 검색</label></h3><div><input id="workflow-search" name="query" value="${escapeHtml(logsFilter)}" placeholder="Workflow ID, Task ID, 기획 목표" /><button class="secondary-button" type="submit">검색</button>${logsFilter ? `<button class="text-button" type="button" id="clear-log-search">초기화</button>` : ""}</div></form>${logList}</section>`}`;
}

function supervisorDecisionDetails(trace) {
  const decisions = trace.flatMap((row) => {
    const source = row.attributes && typeof row.attributes === "object" ? row.attributes : row;
    const nested = Array.isArray(source.supervisor_decisions) ? source.supervisor_decisions : [];
    return [...nested, ...(source.decision_id ? [source] : [])];
  });
  const unique = [...new Map(decisions.filter((item) => item && typeof item === "object")
    .map((item, index) => [item.decision_id ?? `${item.event ?? "decision"}-${index}`, item])).values()];
  if (!unique.length) return "";
  return `<details class="supervisor-decision-details"><summary>Supervisor 결정 근거 ${unique.length}건</summary><div class="decision-detail-list">${unique.map((item) => {
    const transition = [item.from_state, item.to_state].filter(Boolean).join(" → ") || item.workflow_state || "상태 전이 정보 없음";
    const version = item.state_version == null ? "버전 정보 없음" : `v${item.expected_state_version ?? "?"} → v${item.state_version}`;
    const evidence = Array.isArray(item.evidence_refs) ? item.evidence_refs : [];
    return `<article class="decision-detail"><div><b>${escapeHtml(transition)}</b><span>${escapeHtml(version)}</span></div><dl><dt>결정 / 조치</dt><dd>${escapeHtml(item.decision_id ?? "기록된 결정")} · ${escapeHtml(item.action ?? item.supervisor_action ?? "—")}</dd><dt>복구 정책</dt><dd>v${escapeHtml(item.policy_version ?? "—")} · ${escapeHtml(item.matched_rule_id ?? "규칙 정보 없음")}</dd><dt>이유</dt><dd>${escapeHtml(item.reason_code ?? item.reason ?? "—")}</dd><dt>근거 참조</dt><dd>${evidence.length ? evidence.map(escapeHtml).join(" · ") : "저장된 근거 참조 없음"}</dd></dl></article>`;
  }).join("")}</div></details>`;
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

async function workflowControl(action) {
  if (!currentRun?.supervisor_state) return;
  const id = currentRun.workflow_id;
  try {
    const fingerprint = await requestFingerprint({ id, version: currentRun.supervisor_state.state_version });
    const request = pendingRequestKey(`workflow-${action}`, fingerprint);
    const response = await fetch(`/api/v1/workflows/${encodeURIComponent(id)}/${action}`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": request.key },
      body: JSON.stringify({ expected_state_version: currentRun.supervisor_state.state_version }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error("상태가 변경됐거나 기록을 확인할 수 없습니다. 최신 상태를 다시 확인하세요.");
    currentRun = result;
    clearPendingRequestKey(`workflow-${action}`);
    scheduleWorkflowPoll();
  } catch (error) { toast(error.message, true); }
  render();
}

async function selectLoggedWorkflow(workflowId) {
  try {
    const response = await fetch(`/api/v1/workflows/${encodeURIComponent(workflowId)}`);
    const body = await response.json();
    if (!response.ok) throw new Error("선택한 Workflow를 읽지 못했습니다.");
    currentRun = body;
    revisionOpen = false;
    localStorage.setItem("eventquest.latestWorkflow", body.workflow_id);
    scheduleWorkflowPoll();
  } catch (error) { toast(error.message, true); }
  render();
}

async function restoreLatestWorkflow() {
  if (currentRun) return;
  const savedWorkflowId = localStorage.getItem("eventquest.latestWorkflow");
  try {
    const response = await fetch("/api/v1/workflows?limit=1");
    const recent = await response.json();
    if (!response.ok) throw new Error("최근 Workflow를 불러오지 못했습니다.");
    const body = Array.isArray(recent) ? recent[0] : null;
    if (!body?.workflow_id) throw new Error("최근 Workflow가 없습니다.");
    localStorage.setItem("eventquest.latestWorkflow", body.workflow_id);
    if (!currentRun && activeView === "create" && !userStartedCreating) {
      currentRun = body;
      activeView = "workflow";
      render();
    }
  } catch (error) {
    if (!savedWorkflowId) return;
    try {
      const response = await fetch(`/api/v1/workflows/${encodeURIComponent(savedWorkflowId)}`);
      const body = await response.json();
      if (!response.ok) throw new Error("저장된 Workflow를 복원하지 못했습니다.");
      if (!currentRun && activeView === "create" && !userStartedCreating) {
        currentRun = body;
        activeView = "workflow";
        render();
      }
    } catch (restoreError) { toast(restoreError.message, true); }
  }
}

function render() {
  if (!loggedIn) {
    loginScreen();
    return;
  }
  const existingForm = $("#brief-form");
  if (existingForm && activeView === "create") {
    briefDraft = {
      fields: Object.fromEntries(new FormData(existingForm)),
      spaces: [...existingForm.querySelectorAll("[data-space-row]")].map((row) => ({
        id: row.dataset.spaceId, enabled: row.querySelector('[name="space_enabled"]').checked,
      })),
    };
  }
  shell();
  $("#page-title").textContent = titleFor(activeView);
  scheduleWorkflowPoll();
  const content = { create: createView, workflow: workflowView, policies: policiesView, fault: faultView, logs: logsView }[activeView]();
  $("#view-root").innerHTML = content;
  $("#view-root").dataset.view = activeView;
  if (activeView === "create" && briefDraft && $("#brief-form")) {
    for (const name of ["objective", "segment", "operation_mode", "duration", "impact"]) {
      const field = $(`[name="${name}"]`);
      if (field && briefDraft.fields[name] !== undefined) field.value = briefDraft.fields[name];
    }
    for (const saved of briefDraft.spaces) {
      const row = [...document.querySelectorAll("[data-space-row]")].find((item) => item.dataset.spaceId === saved.id);
      if (!row) continue;
      row.querySelector('[name="space_enabled"]').checked = saved.enabled;
      row.classList.toggle("disabled", !saved.enabled);
    }
  }
  $("#brief-form")?.addEventListener("submit", submitBrief);
  $("#brief-form")?.addEventListener("input", (event) => {
    userStartedCreating = true;
    const field = event.target;
    if (!(field instanceof HTMLElement)) return;
    field.removeAttribute("aria-invalid");
    field.nextElementSibling?.classList.contains("field-validation-error") && field.nextElementSibling.remove();
  });
  $("#brief-form")?.querySelectorAll('[name="space_enabled"]').forEach((checkbox) => checkbox.addEventListener("change", () => {
    const row = checkbox.closest("[data-space-row]");
    row.classList.toggle("disabled", !checkbox.checked);
  }));
  $("#operation-mode")?.addEventListener("change", (event) => {
    const ongoing = event.currentTarget.value === "ongoing";
    $("#duration-label").textContent = ongoing ? "달성 가능성 평가 기간 (일)" : "퀘스트 기간 (일)";
    $("#duration-hint").textContent = ongoing ? "상시 퀘스트의 달성 가능성을 평가할 기준 기간입니다. Runtime은 종료 시각 없이 운영합니다." : "기간 한정 퀘스트의 실제 운영 기간입니다.";
  });
  $("#reload-planning-catalog")?.addEventListener("click", () => { planningCatalogError = ""; loadPlanningCatalog(); });
  $("#run-fault")?.addEventListener("click", runFaultScenario);
  if (activeView === "fault") mountFaultLab();
  document.querySelector("[data-approve-plan]")?.addEventListener("click", () => decideCurrentPlan("APPROVE"));
  document.querySelector("[data-open-revision]")?.addEventListener("click", () => { revisionOpen = !revisionOpen; render(); $("#revision-comment")?.focus(); });
  document.querySelector("#revision-form")?.addEventListener("submit", submitRevision);
  document.querySelector("[data-revised-workflow]")?.addEventListener("click", (event) => selectLoggedWorkflow(event.currentTarget.dataset.revisedWorkflow));
  document.querySelector("[data-reject-plan]")?.addEventListener("click", () => decideCurrentPlan("REJECT"));
  document.querySelector("[data-apply-plan]")?.addEventListener("click", applyCurrentPlan);
  document.querySelector("[data-refresh-runtime-progress]")?.addEventListener("click", () => {
    const runtimeEvent = currentRun?.runtimeEvent ?? currentRun?.runtime_event;
    if (runtimeEvent?.runtime_event_id) loadRuntimeProgress(runtimeEvent.runtime_event_id);
  });
  if (activeView === "policies" && !policyState && !policiesLoading && !policyLoadError) loadPolicies();
  if (activeView === "logs" && logsState === null) loadLogs();
  if (activeView === "create" && !planningCatalog && !planningCatalogLoading && !planningCatalogError) loadPlanningCatalog();
  $("#policy-search")?.addEventListener("input", (event) => {
    policyFilter = event.currentTarget.value;
    const query = policyFilter.trim().toLocaleLowerCase();
    const rows = [...document.querySelectorAll("[data-policy-row]")];
    let visibleCount = 0;
    for (const row of rows) {
      const actionSelect = row.querySelector("[data-policy-action]");
      const code = actionSelect?.dataset.policyAction;
      const definition = policyState?.error_catalog?.[code];
      const actionLabel = actionSelect?.selectedOptions[0]?.textContent ?? "";
      const searchText = [row.querySelector(".policy-error-label")?.textContent, code,
        definition?.category, definition?.location, actionLabel]
        .join(" ").toLocaleLowerCase();
      row.hidden = Boolean(query && !searchText.includes(query));
      if (!row.hidden) visibleCount += 1;
    }
    $("#policy-search-count").textContent = `${visibleCount}개 / ${rows.length}개 표시`;
    $(".policy-filter-empty").hidden = visibleCount > 0;
  });
  $("#cancel-workflow")?.addEventListener("click", () => workflowControl("cancel"));
  $("#reconcile-workflow")?.addEventListener("click", () => workflowControl("reconcile"));
  $("#save-policy-draft")?.addEventListener("click", savePolicyDraft);
  $("#reload-policies")?.addEventListener("click", () => { policyLoadError = ""; loadPolicies(); });
  $("#validate-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/validate`));
  $("#preview-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/preview`));
  $("#activate-policy")?.addEventListener("click", (event) => policyAction(`/${event.currentTarget.dataset.version}/activate`));
  document.querySelectorAll("[data-rollback]").forEach((button) => button.addEventListener("click", () => policyAction(`/${button.dataset.rollback}/rollback`)));
  $("#refresh-logs")?.addEventListener("click", () => { logsState = null; loadLogs(); });
  $("#log-search-form")?.addEventListener("submit", (event) => { event.preventDefault(); logsFilter = new FormData(event.currentTarget).get("query")?.toString() ?? ""; render(); });
  $("#clear-log-search")?.addEventListener("click", () => { logsFilter = ""; render(); });
  document.querySelectorAll("[data-workflow-id]").forEach((button) => button.addEventListener("click", () => selectLoggedWorkflow(button.dataset.workflowId)));
  document.querySelectorAll("[data-goto]").forEach((button) => button.addEventListener("click", () => { activeView = button.dataset.goto; render(); }));
  showToast();
  const appliedEvent = activeView === "workflow" ? currentRun?.runtimeEvent ?? currentRun?.runtime_event : null;
  if (appliedEvent?.runtime_event_id && runtimeProgressEventId !== appliedEvent.runtime_event_id && !runtimeProgressLoading) {
    loadRuntimeProgress(appliedEvent.runtime_event_id);
  }
}

render();
if (loggedIn) restoreLatestWorkflow();
