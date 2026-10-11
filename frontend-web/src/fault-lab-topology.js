// The backend owns topology and fault definitions; this module only renders them.
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function fixedGraph(topology, observed, result) {
  const layout=topology.layout;
  const byId=new Map(topology.nodes.map(n=>[n.node_id,n]));
  const injected=Boolean(result?.checks?.some(c=>c.name==="detector_evidence" && c.actual===true));
  const faultNode=topology.nodes.find(n=>n.target_ids.includes(result?.scenario?.target_id));
  const paths=topology.edges.map((edge,i)=>{
    const a=byId.get(edge.from).position,b=byId.get(edge.to).position;
    let path;
    if (edge.kind === "revision") {
      const sx=a.x,sy=a.y+a.height/2,tx=b.x+b.width,ty=b.y+b.height/2;
      path=`M${sx},${sy} C${sx-26},${sy} ${tx+26},${ty} ${tx},${ty}`;
    } else if (a.x===b.x) {
      path=`M${a.x+a.width/2},${a.y+a.height} L${b.x+b.width/2},${b.y}`;
    } else if (a.y===b.y) {
      path=`M${a.x+a.width},${a.y+a.height/2} L${b.x},${b.y+b.height/2}`;
      if (a.x>b.x) path=`M${a.x},${a.y+a.height/2+18} L${b.x+b.width},${b.y+b.height/2+18}`;
    } else {
      const sx=a.x+a.width/2,sy=a.y+a.height,tx=b.x+b.width/2,ty=b.y;
      const middle=sy+(ty-sy)/2;
      path=`M${sx},${sy} C${sx},${middle} ${tx},${middle} ${tx},${ty}`;
      if(edge.kind === "recovery") path=`M${a.x},${a.y+18} C${a.x-18},${a.y-40} ${b.x+b.width+24},${b.y-40} ${b.x+b.width},${b.y+18}`;
    }
    if(edge.kind === "control") path=`M${a.x+a.width},${a.y+32} L${a.x+a.width+22},${a.y+32} L${a.x+a.width+22},${b.y+48} L${b.x},${b.y+48}`;
    if(edge.kind === "recovery" && edge.to==="planner") path=`M${a.x},${a.y+72} L${a.x-24},${a.y+72} L${a.x-24},${a.y+a.height+14} L${b.x+b.width+16},${a.y+a.height+14} L${b.x+b.width+16},${b.y+72} L${b.x+b.width},${b.y+72}`;
    const active=result?.recovered && edge.kind==="recovery" && edge.to===faultNode?.node_id;
    return `<path data-edge-from="${escape(edge.from)}" data-edge-to="${escape(edge.to)}" class="lab-edge ${escape(edge.kind)} ${active?"active":""}" d="${path}" marker-end="url(#lab-arrow-${edge.kind==="stop"?"stop":edge.kind==="flow"?"flow":"control"})"><title>${escape(edge.label??edge.kind)}</title></path>`;
  }).join("");
  const cards=topology.nodes.map(n=>{
    const p=n.position;
    const failure=injected && faultNode?.node_id===n.node_id;
    const stopped=n.node_id==="safe_stop" && ["SAFE_STOP","FAILED"].includes(result?.actual_terminal);
    return `<foreignObject x="${p.x}" y="${p.y}" width="${p.width}" height="${p.height}"><button xmlns="http://www.w3.org/1999/xhtml" class="fault-node ${selectedNode===n.node_id?"selected":""} ${observed.has(n.node_id)?"observed":""} ${failure?"fault-detected":""} ${stopped?"stopped":""}" aria-pressed="${selectedNode===n.node_id}" data-fault-node="${escape(n.node_id)}"><small>${failure?"오류 검출 위치":n.kind==="supervisor"?"전체 단계의 상태·전이·복구 관리":n.kind==="terminal"?"종료 / 대기":n.target_ids.length?"시험 위치 선택 가능":"관측 단계"}</small><b>${escape(n.label)}</b><span>${selectedNode===n.node_id?"선택한 단계":stopped?"안전 중단 확인":observed.has(n.node_id)?"실제 기록 확인":"실행 전에도 표시되는 고정 단계"}</span></button></foreignObject>`;
  }).join("");
  return `<div class="fault-graph-scroll" tabindex="0" aria-label="고정 Workflow 그래프 · 작은 화면에서는 좌우로 탐색"><svg class="fault-topology" viewBox="0 0 ${layout.width} ${layout.height}" width="${layout.width}" height="${layout.height}" aria-label="전체 고정 Workflow"><defs>${["flow","control","stop"].map(k=>`<marker id="lab-arrow-${k}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="${k==="stop"?"#ee968c":k==="flow"?"#8ecbb8":"#bb9cff"}"/></marker>`).join("")}</defs>${layout.lanes.map(l=>`<text x="${l.x}" y="30" class="lab-lane-title">${escape(l.label)}</text>`).join("")}${paths}${cards}</svg></div>`;
}
let catalog = null;
let selectedTopology = "canonical_planning";
let selectedNode = "planner";
let selectedTarget = "planner.response";
let selectedScenario = "planner_output_invalid";
let current = null;
let selectedResultIndex = -1;
let showExecutionPath = false;
let advancedOpen = false;
let history = JSON.parse(sessionStorage.getItem("eventquest.faultLabHistory") ?? "[]");
let selectedOptions = {pattern:"once",first_n:1,profile:"current_active"};
let poll = null;
let error = "";
const rememberedRequest=JSON.parse(sessionStorage.getItem("eventquest.faultLabPending") ?? "null");
let pendingKey = rememberedRequest?.key ?? null;
let pendingBody = rememberedRequest?.body ?? null;


const actionLabels = {RUN_AGENT:"기획안 재작성 후 재검증",TOOL_FALLBACK:"검증된 대체 게임 규칙 조회",STOP:"안전 중단",RETRY:"같은 작업 재시도",SAFE_STOP:"안전 중단",FALLBACK:"대체 경로 사용",REPLAN:"기획안 재작성",REVISE:"기획안 수정",NEEDS_DATA:"추가 데이터 요청",NEEDS_DEVELOPMENT:"개발 필요 표시"};
const terminalLabels = {REQUEST_BLOCKED:"입력 요청 거절",COMPLETED:"처리 완료",WAITING_APPROVAL:"기획안 작성 완료 · 사람 승인 대기",SAFE_STOP:"안전 중단",FAILED:"작업 실패 종료 (FAILED) · 시험 판정과 별도",NEEDS_DATA:"추가 데이터 필요",NEEDS_DEVELOPMENT:"개발 필요",CANCELLED:"취소",REJECTED:"요청 거절"};
const checkLabels = {input_rejection_contract:"입력 거절 원인 계약",trace_pinned_policy:"고정된 정책 버전 유지",trace_policy_hash:"정책 참조 해시 일치",recovery_rule_reference:"오류 대응 규칙 근거",state_version_chain:"상태 버전·전이 순서",terminal_no_further_execution:"종료 후 추가 작업 차단",fallback_calls:"대체 조회 횟수",injection_applied:"선택한 오류 주입",detector_evidence:"실제 오류 검출",terminal:"예상한 최종 상태",target_attempts:"허용된 시도 횟수",runtime_unchanged:"게임 데이터 보존",forbidden_runtime_calls:"금지된 게임 쓰기 차단",policy_recovery_actions:"정책에 따른 대응",fixed_request:"원래 요청 유지",fixed_actions:"유저 행동 계약 유지",fixed_rewards:"보상 계약 유지",fixed_reward_links:"행동과 보상 연결 유지",critical_trace_mapping:"필수 실행 기록 확인",idempotent_workflow_replay:"중복 실행 방지",run_owned_result_rows:"시험 결과 저장 범위",policy_restored:"시험 후 원래 정책 복원"};
function resultSummary(result) {
  if (result.provider_mode === "live") {
    const attempts=result.provider_attempts ?? [];
    const measured=attempts.filter(a=>a.status==="MEASURED");
    const unknown=attempts.filter(a=>a.status!=="MEASURED").length;
    const normal=result.experiment_kind==="normal";
    const tokens=measured.reduce((n,a)=>n+a.input_tokens+a.output_tokens,0);
    const cost=measured.reduce((n,a)=>n+Number(a.cost),0);
    const rows=[
      ["시험 종류",normal ? "실제 LLM 정상 기획 · 오류 주입 없음" : result.experiment_kind==="wire" ? "실제 MCP 통신 장애 · 실제 LLM 후속 처리" : "실제 LLM 응답/호출 경계 상황 주입"],
      ["최종 상태",terminalLabels[result.actual_terminal] ?? "확인 불가"],
      ["복구 결과",normal ? "정상 기획 계약 검증" : result.recovered ? "오류 대응 후 승인 대기까지 진행" : "복구 완료 확인 안 됨 · 정책 종료/불일치 확인"],
      ["시험 판정",`${result.verdict} · 아래 독립 검증 항목과 원본 Trace를 확인하세요.`],
      ["실제 호출 근거",`사용량 확인 ${measured.length}회 · 사용량 불명 ${unknown}회 · 확인된 토큰 ${tokens} · 설정 단가 기준 $${cost.toFixed(6)} (청구액 확정 아님)`],
      ["정책",`고정 정책 버전 ${result.policy_ref?.version ?? "확인 불가"} · 원복 ${result.cleanup_ok ? "확인" : "확인 안 됨"}`],
    ];
    return `<div class="lab-result-summary" aria-label="시험 결과 설명">${rows.map(([title,value])=>`<div class="log-row"><b>${escape(title)}</b><span>${escape(value)}</span></div>`).join("")}</div>`;
  }
  const checks=result.checks ?? [];
  const check=(name)=>checks.find(c=>c.name===name);
  const actions=check("policy_recovery_actions")?.actual ?? [];
  const decisions=(result.trace ?? []).filter(e=>e.error_code && (e.event?.includes("decided") || e.action));
  const decisionActions=actions.length ? actions : decisions.map(e=>e.action ?? e.status).filter(Boolean);
  const stopped=["SAFE_STOP","FAILED"].includes(result.actual_terminal);
  const fault=result.scenario?.fault_id;
  const input=fault==="input_validation_error" || fault==="input_guard_blocked";
  const scope=fault==="unsupported_capability" || fault==="game_data_unavailable";
  const conflict=fault==="supervisor_state_conflict";
  const reconciled=fault==="db_write_outcome_unknown";
  const finding=(result.business_findings ?? []).find(f=>f.blocks_runtime);
  let finalLabel=terminalLabels[result.actual_terminal] ?? result.actual_terminal ?? "확인 불가";
  if(scope) finalLabel=finding?.repairability==="NEEDS_DATA" || fault==="game_data_unavailable" ? "추가 게임 데이터 필요 · 게임 적용 차단" : "지원 기능 개발 필요 · 게임 적용 차단";
  if(conflict) finalLabel="상태 충돌 요청 거절 · 기존 작업의 상태 유지 여부 검증";
  const outcome=input ? "Workflow 시작 전에 입력 검사를 거쳤습니다." : scope ? "업무 판정 결과이며 자동 오류 복구 성공과 구분합니다." : conflict ? "충돌 요청을 재시도하거나 기존 작업을 변경하지 않습니다." : reconciled && result.recovered ? "저장 결과를 조회해 확인했습니다. 쓰기를 재실행한 것이 아닙니다." : result.recovered ? "자동 복구 후 작업을 계속했습니다." : stopped ? "작업을 중단했습니다. 정책과 일치했는지는 아래 시험 판정에서 확인합니다." : "자동 복구 성공으로 집계되지 않은 결과입니다.";
  const verdict=result.verdict === "PASS" ? "PASS · 기대한 정책 대응과 검증 기준에 일치합니다. 안전 중단도 기대 결과이면 PASS입니다." : result.verdict === "FAIL" ? "FAIL · 기대 결과와 다른 항목이 있습니다. 아래 불일치 항목을 확인하세요." : `${result.verdict ?? "판정 대기"} · 복구 성공 여부를 확정할 수 없습니다.`;
  const observedCode=result.observed_error_code ?? result.scenario?.observed_code ?? result.scenario?.code;
  const codeDescription=observedCode!==result.scenario?.code ? `선택 코드 ${result.scenario?.code} · 실제 관측 원인 ${observedCode}` : result.scenario?.code ?? "코드 없음";
  const policyDescription=input ? "입력 검사에서 거절 · Workflow 복구 정책 결정 전 단계" : scope ? "지원 범위·데이터 검증의 업무 판정 · 오류 재시도 대상 아님" : conflict ? "상태 버전 검사에서 충돌 거절 · 기존 작업 복구와 구분" : reconciled ? "저장 결과 재조정 · 불명확한 쓰기 재시도 금지" : decisionActions.length ? decisionActions.map(a=>actionLabels[a] ?? a).join(" → ") : "오류 대응 결정 기록 없음";
  const rows=[
    ["1. 주입한 오류",`${result.scenario?.safe_message ?? "오류 정보 없음"} (${codeDescription}) · ${catalog.targets.find(t=>t.target_id===result.scenario?.target_id)?.label ?? result.scenario?.target_id ?? "위치 정보 없음"} · ${check("injection_applied")?.actual === true ? "주입 확인" : "주입 확인 안 됨"}`],
    ["2. 검출 결과",check("detector_evidence")?.actual === true ? (input ? `실제 입력 거절 응답 확인 · HTTP ${result.http_evidence?.status ?? "상세 기록 참조"}` : scope ? "실제 게임 근거로 업무 판정 기록을 확인했습니다." : "실제 처리 코드에서 오류 검출 기록을 확인했습니다.") : "실제 오류 검출 근거를 확인하지 못했습니다."],
    ["3. 정책 결정",`${policyDescription} · 시험에 고정한 정책 버전 ${result.policy_ref?.version ?? "확인 불가"}`],
    ["4. 최종 상태",`${finalLabel}. ${outcome}`],
    ["5. 시험 판정",verdict],
  ];
  const counts=fault==="mcp_game_rules_error" ? `Primary 조회 ${result.attempts ?? "확인 불가"}회 · 별도 콘텐츠 조회를 포함하며 재시도 횟수와 다릅니다.` : result.plan_revisions > 0 ? `해당 경계 관측 ${result.attempts ?? "확인 불가"}회 · 기획 수정 후 재검증을 포함합니다.` : `해당 작업의 최초 시도 ${result.attempts > 0 ? 1 : 0}회 + 재시도 ${result.retries ?? 0}회 · 전체 시도 ${result.attempts ?? "확인 불가"}회`;
  return `<div class="lab-result-summary" aria-label="시험 결과 설명">${rows.map(([title,value])=>`<div class="log-row"><b>${escape(title)}</b><span>${escape(value)}</span></div>`).join("")}</div><p>${escape(counts)}</p><p>재시도 ${result.retries ?? 0}회 · 기획 수정 ${result.plan_revisions ?? 0}회 · 대체 조회 ${result.fallback_calls ?? 0}회. 재시도·수정·대체 조회는 서로 다른 동작입니다.</p>`;
}
function rememberRun(run) {
  history=[{id:run.id,kind:run.kind},...history.filter(r=>r.id!==run.id)].slice(0,30);
  sessionStorage.setItem("eventquest.faultLabHistory",JSON.stringify(history));
  sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({id:run.id,kind:run.kind}));
}

export function renderFaultLab() {
  return `<div class="page-intro"><div><span class="eyebrow">ISOLATED FAULT LAB</span><h2>오류를 선택하고 실제 복구를 확인합니다.</h2><p>주입 → 실제 검출 → Supervisor 정책 결정 → 결과 검증을 분리해 보여줍니다.</p></div><span class="state-chip warning-chip">시험 DB · 별도 프로세스</span></div><div id="fault-lab"><section class="panel">시험 환경을 확인하는 중입니다…</section></div>`;
}

export async function mountFaultLab() {
  const host = document.querySelector("#fault-lab");
  if (!host) return;
  if (!catalog) {
    try {
      const response = await fetch("/api/test/fault-catalog");
      if (!response.ok) throw new Error(response.status === 404 ? "일반 사용자 서버입니다. 격리 시험실을 실행한 뒤 http://127.0.0.1:5183에서 접속하세요." : "시험 DB 설정·소유권을 확인하세요.");
      catalog = await response.json();
    } catch (failure) {
      host.innerHTML = `<section class="panel warning-note"><h3>격리 시험실 연결 필요</h3><p>${escape(failure.message)}</p><code>powershell -File infra/fault-lab-local.ps1</code></section>`;
      return;
    }
  }
  if (!host.isConnected) return;
  const remembered=JSON.parse(sessionStorage.getItem("eventquest.faultLabRun") ?? "null");
  if (remembered && !current) {
    const response=await fetch(`/api/test/fault-${remembered.kind === "suite" ? "suites" : "runs"}/${remembered.id}`);
    if(response.ok) {current=await response.json();rememberRun(current);}
  }
  draw(host);
  if (current && ["QUEUED","RUNNING"].includes(current.lifecycle)) resumePoll(host);
}

function draw(host) {
  const topology = catalog.topologies.find((t) => t.topology_id === selectedTopology);
  const node = topology.nodes.find((n) => n.node_id === selectedNode);
  const availableTargets=catalog.targets.filter(t=>node.target_ids.includes(t.target_id) && t.supported_fault_ids.length);
  if(!availableTargets.some(t=>t.target_id===selectedTarget)) selectedTarget=availableTargets[0]?.target_id;
  const target=availableTargets.find(t=>t.target_id===selectedTarget);
  const matching = catalog.scenarios.filter((s) => s.target_id===selectedTarget);
  if (!matching.some((s) => s.scenario_id === selectedScenario)) selectedScenario = matching[0]?.scenario_id;
  const scenario = matching.find((s) => s.scenario_id === selectedScenario);
  const ready = catalog.scenarios.filter((s) => s.readiness === "ready");
  const result = current?.results?.at(selectedResultIndex);
  const graphResult = showExecutionPath && result?.scenario?.route===topology.route ? result : null;
  const trace = graphResult?.trace ?? [];
  const observed = new Set(topology.nodes.filter((n) => trace.some((e) => n.observed_events.includes(e.event ?? e.action))).map((n) => n.node_id));
  host.innerHTML = `<section class="panel"><div class="panel-heading"><h3>전체 Workflow · 위치 선택</h3><span class="link-badge">${escape(topology.version)}</span></div>
    <p>${selectedTopology === "canonical_planning" ? "기획안 작성 흐름" : "고급 시험 · 기존 Task 흐름"}${showExecutionPath ? " · 선택한 결과의 실제 실행 기록 보기" : " · 시험 위치를 선택하세요"}</p>
    <details id="lab-advanced" ${advancedOpen ? "open" : ""}><summary>고급 설정 · 기존 Task 오류 시험</summary><label>시험 흐름<select id="lab-topology">${catalog.topologies.filter(t=>["canonical_planning","legacy_task"].includes(t.topology_id)).map(t=>`<option value="${escape(t.topology_id)}" ${t.topology_id===selectedTopology ? "selected" : ""}>${t.topology_id==="canonical_planning" ? "기획안 작성 흐름 (기본)" : "기존 Task 흐름 · 업무 검증 오류 3개"}</option>`).join("")}</select></label><p>승인 후 런타임 쓰기 오류는 전용 시험 실행이 지원되지 않아 이 화면의 선택 대상에서 제외됩니다.</p></details>${topology.note ? `<p class="warning-note">${escape(topology.note)}</p>` : ""}${fixedGraph(topology,observed,graphResult)}
    ${showExecutionPath ? `<button class="secondary-button" id="lab-back-result">시험 결과로 돌아가기</button>` : ""}
    <details><summary>전체 경로와 분기 설명</summary><div class="fault-edges">${topology.edges.map((e) => `<span>${escape(topology.nodes.find((n) => n.node_id === e.from).label)} → ${escape(topology.nodes.find((n) => n.node_id === e.to).label)}${e.kind !== "flow" ? ` · ${escape(e.label ?? e.kind)}` : ""}</span>`).join("")}</div></details>
    <p>노드 위치는 실행·재시도 중에도 고정됩니다. 완료된 실행은 결과의 ‘실행 경로 보기’를 눌러 확인합니다. 실행 중에는 확인되지 않은 진행을 표시하지 않습니다. 보라색 테두리: 선택 · 초록: 실제 기록 · 주황: 오류 위치. 점선은 수정·복구 경로입니다.</p></section>
    <section class="panel form-panel"><h3>${escape(node.label)}에서 오류 시험</h3>
    <p class="fault-selection" role="status">선택한 위치: <strong>${escape(node.label)}</strong>${target ? ` → ${escape(target.label)}` : " · 관측 전용"}</p>
    ${matching.length ? `<label>이 단계의 세부 위치<select id="lab-target">${availableTargets.map(t=>`<option value="${escape(t.target_id)}" ${t.target_id===selectedTarget?"selected":""}>${escape(t.label)}</option>`).join("")}</select></label><label>선택한 위치에서 발생시킬 원인<select id="lab-scenario">${matching.map((s) => `<option value="${escape(s.scenario_id)}" ${s.scenario_id === selectedScenario ? "selected" : ""}>${escape(s.safe_message)}</option>`).join("")}</select></label>
    <details><summary>위치·오류 계약 상세</summary><p>${escape(scenario.code)} · ${escape(target.target_id)}</p><code>${escape(target.boundary_ref)}</code></details>
    <label>주입 패턴<select id="lab-pattern">${scenario.patterns.map((p) => `<option value="${p}">${{once:"첫 호출 1회",first_n:"처음 N회",always:"계속 발생"}[p]}</option>`).join("")}</select></label>
    <label>처음 N회<input id="lab-first-n" type="number" min="1" max="4" value="1" /></label>
    <label>시험 정책<select id="lab-profile">${scenario.profiles.map((p) => `<option value="${p}">${{current_active:"현재 활성 정책",retry_allowed:"허용된 자동 복구 정책",safe_stop:"안전 종료 정책"}[p]}</option>`).join("")}</select></label>
    <p>고정 입력과 실제 게임 DB/MCP를 사용합니다. 기본 LLM 응답은 결정적 adapter로 생성하며 외부 호출 비용은 발생하지 않습니다.</p>
    <button id="lab-run" class="primary-button" ${current?.lifecycle === "RUNNING" || current?.lifecycle === "QUEUED" ? "disabled" : ""}>선택한 오류 실행</button>` : `<p>이 단계는 전용 시험 runner 연결이 필요합니다. 아래 전체 목록에서 준비 상태를 확인하세요.</p>`}
    <button id="lab-suite" class="secondary-button" ${["RUNNING","QUEUED"].includes(current?.lifecycle) ? "disabled" : ""}>전체 ${ready.length}개 시나리오 · 안전 종료 시험</button>
    <p>전체 시험에는 기획안 작성 흐름과 기존 Task 업무 검증 오류 3개가 포함됩니다.</p>
    ${history.length ? `<label>이 탭에서 요청한 시험 기록<select id="lab-history" ${["RUNNING","QUEUED"].includes(current?.lifecycle) ? "disabled" : ""}><option value="">기록 선택 · 조회만 수행</option>${history.map(r=>`<option value="${escape(r.id)}">${r.kind === "suite" ? "전체/묶음 시험" : "개별 시험"} · ${escape(r.id)}</option>`).join("")}</select></label>` : ""}
    <label>CLI 개별 시험 기록 ID<input id="lab-record-id" placeholder="실행 결과의 UUID · 조회만 수행" ${["RUNNING","QUEUED"].includes(current?.lifecycle) ? "disabled" : ""}></label><button id="lab-load-record" class="secondary-button" ${["RUNNING","QUEUED"].includes(current?.lifecycle) ? "disabled" : ""}>기록 ID로 조회</button><p>실제 LLM 시험은 CLI에서 명시적으로 실행합니다. 이 화면의 조회는 LLM을 재호출하지 않습니다.</p>
    ${error ? `<p class="warning-note" role="alert">${escape(error)}</p>` : ""}</section>
    ${current ? `<section class="panel" id="lab-report"><div class="panel-heading"><h3>시험 ${escape(current.lifecycle)} · ${escape(current.verdict ?? "판정 대기")}</h3><span>${escape(current.id)}</span></div>${["QUEUED","RUNNING"].includes(current.lifecycle) ? `<p role="status">시험을 실행하고 있습니다. 아직 확인되지 않은 단계는 그래프에 표시하지 않습니다.</p>` : ""}
    ${current.results?.length > 1 ? `<label>개별 시험 결과<select id="lab-result"><option value="-1" ${selectedResultIndex === -1 ? "selected" : ""}>최근 완료 결과</option>${current.results.map((r,i)=>`<option value="${i}" ${selectedResultIndex === i ? "selected" : ""}>${i+1}. ${escape(r.scenario?.safe_message ?? "시험")} · ${escape(r.verdict)}</option>`).join("")}</select></label>` : ""}
    ${current.counts ? `<p>선택 ${current.counts.selected} · 실행 ${current.counts.executed} · 통과 ${current.counts.PASS} · 실패 ${current.counts.FAIL} · 미실행 ${current.counts.NOT_EXECUTED} · 판정 불가 ${current.counts.INCONCLUSIVE}</p>` : ""}
    ${result ? `${resultSummary(result)}<button class="secondary-button" id="lab-path" aria-pressed="${showExecutionPath}">${showExecutionPath ? "실행 경로 숨기기" : "실행 경로 보기"}</button>` : ""}
    ${result?.warnings?.length ? `<details><summary>UNMAPPED_EVENT · 미매핑 관측 ${result.warnings.length}개</summary><pre>${escape(JSON.stringify(result.warnings,null,2))}</pre></details>` : ""}
    ${result?.observations?.length ? `<details><summary>보조 관측 정보 · ${result.observations.length}개</summary><p>LLM 사용량과 결과 저장 기록입니다. 그래프 단계나 오류 매핑 경고와 구분하며 원본 Trace에 보존합니다.</p><pre>${escape(JSON.stringify(result.observations,null,2))}</pre></details>` : ""}
    <details><summary>검증 항목 ${result?.checks?.length ? (result.checks.some(c=>!c.passed) ? `· 불일치 ${result.checks.filter(c=>!c.passed).length}개` : `· ${result.checks.length}개 일치`) : "· 판정 대기"}</summary><div class="fault-checks">${(result?.checks ?? []).map((c) => `<div class="log-row"><b>${c.passed ? "✓" : "!"} ${escape(checkLabels[c.name] ?? c.name)}</b><span>${c.passed ? "일치" : "기대·실제 불일치"}</span>${!c.passed ? `<details><summary>기대값과 실제값</summary><pre>${escape(JSON.stringify({expected:c.expected,actual:c.actual},null,2))}</pre></details>` : ""}</div>`).join("")}</div></details>
    <details><summary>주입·검출·정책·실행 Trace 상세</summary><p>내부 경계 호출 ${result?.boundary_calls ?? "확인 불가"}회 · 재시도 횟수와 구분되는 기록입니다.</p><pre>${escape(JSON.stringify(current, null, 2))}</pre></details>
    <button class="secondary-button" id="lab-download">JSON 보고서 저장</button></section>` : ""}
    <section class="panel"><h3>전체 오류 카탈로그 · ${new Set(catalog.scenarios.map((s)=>s.fault_id)).size}개</h3><p>오류 코드 ${new Set(catalog.scenarios.map(s=>s.code)).size}개 · 시험 시나리오 ${catalog.scenarios.length}개 · 실행 가능 ${ready.length}개 · 전용 runner 필요 ${catalog.scenarios.length-ready.length}개. 미실행 항목은 통과율에 포함하지 않습니다.</p>${catalog.scenarios.map((s) => `<div class="log-row"><span>${escape(s.code)}</span><span>${escape(s.safe_message)}</span><b>${s.readiness === "ready" ? "실행 가능" : "runner 연결 필요"}</b></div>`).join("")}</section>`;
  const patternInput=host.querySelector("#lab-pattern"),profileInput=host.querySelector("#lab-profile"),nInput=host.querySelector("#lab-first-n");
  if (scenario) {
    patternInput.value=scenario.patterns.includes(selectedOptions.pattern) ? selectedOptions.pattern : scenario.patterns[0];
    profileInput.value=scenario.profiles.includes(selectedOptions.profile) ? selectedOptions.profile : scenario.profiles[0];
    nInput.value=selectedOptions.first_n;
    [patternInput,profileInput,nInput].forEach((input)=>input.addEventListener("change",()=>{selectedOptions={pattern:patternInput.value,profile:profileInput.value,first_n:Number(nInput.value)};}));
  }
  host.querySelector("#lab-advanced")?.addEventListener("toggle",e=>{advancedOpen=e.target.open;});
  host.querySelector("#lab-load-record")?.addEventListener("click",async()=>{
    const id=host.querySelector("#lab-record-id").value.trim();
    if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)){error="시험 기록 UUID를 입력하세요.";draw(host);return;}
    try {
      const response=await fetch(`/api/test/fault-runs/${id}`);
      if(!response.ok)throw new Error("개별 시험 기록 조회 실패");
      current=await response.json();error="";selectedResultIndex=-1;showExecutionPath=false;rememberRun(current);clearTimeout(poll);draw(host);
      if(["QUEUED","RUNNING"].includes(current.lifecycle))resumePoll(host);
    } catch(failure) {error=failure.message;draw(host);}
  });
  host.querySelector("#lab-path")?.addEventListener("click",()=>{showExecutionPath=!showExecutionPath;if(showExecutionPath && result){selectedTarget=result.scenario.target_id;selectedScenario=result.scenario.scenario_id;selectedTopology=catalog.topologies.find(t=>t.route===result.scenario.route)?.topology_id ?? "canonical_planning";advancedOpen=selectedTopology!=="canonical_planning";selectedNode=catalog.topologies.find(t=>t.topology_id===selectedTopology).nodes.find(n=>n.target_ids.includes(result.scenario.target_id))?.node_id ?? "planner";}draw(host);if(showExecutionPath)host.querySelector(".fault-graph-scroll")?.scrollIntoView({block:"start"});});
  host.querySelector("#lab-back-result")?.addEventListener("click",()=>host.querySelector("#lab-report")?.scrollIntoView({block:"start"}));
  host.querySelector("#lab-history")?.addEventListener("change",async(e)=>{const entry=history.find(r=>r.id===e.target.value);if(!entry)return;try{const response=await fetch(`/api/test/fault-${entry.kind === "suite" ? "suites" : "runs"}/${entry.id}`);if(!response.ok)throw new Error("시험 기록 조회 실패");current=await response.json();selectedResultIndex=-1;showExecutionPath=false;rememberRun(current);clearTimeout(poll);draw(host);if(["QUEUED","RUNNING"].includes(current.lifecycle))resumePoll(host);}catch(failure){error=failure.message;draw(host);}});
  host.querySelector("#lab-topology")?.addEventListener("change", (e) => {showExecutionPath=false;selectedTopology=e.target.value;selectedNode=catalog.topologies.find((t) => t.topology_id === selectedTopology).nodes[0].node_id;draw(host);});
  host.querySelectorAll("[data-fault-node]").forEach((button) => button.addEventListener("click", () => { showExecutionPath=false;selectedNode = button.dataset.faultNode; draw(host); }));
  host.querySelector("#lab-target")?.addEventListener("change",(e)=>{showExecutionPath=false;selectedTarget=e.target.value;draw(host);});
  host.querySelector("#lab-scenario")?.addEventListener("change", (e) => { showExecutionPath=false;selectedScenario=e.target.value; draw(host); });
  host.querySelector("#lab-run")?.addEventListener("click", () => execute(host, {scenario_id:selectedScenario,pattern:host.querySelector("#lab-pattern").value,first_n:Number(host.querySelector("#lab-first-n").value),profile:host.querySelector("#lab-profile").value}, false));
  host.querySelector("#lab-suite")?.addEventListener("click", () => execute(host, {cases:ready.map((s) => ({scenario_id:s.scenario_id,pattern:s.patterns[0],first_n:1,profile:s.profiles.includes("safe_stop") ? "safe_stop" : s.profiles[0]}))}, true));
  host.querySelector("#lab-result")?.addEventListener("change",(e)=>{showExecutionPath=false;selectedResultIndex=Number(e.target.value);draw(host);});
  host.querySelector("#lab-download")?.addEventListener("click", () => {
    const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download=`fault-${current.id}.json`;a.click();URL.revokeObjectURL(url);
  });
}

async function execute(host, body, suite) {
  try {
    error="";
    showExecutionPath=false;
    selectedResultIndex=-1;
    const serialized=JSON.stringify(body);
    if (pendingBody !== serialized) {pendingBody=serialized;pendingKey=crypto.randomUUID();}
    sessionStorage.setItem("eventquest.faultLabPending",JSON.stringify({body:pendingBody,key:pendingKey}));
    const response=await fetch(`/api/test/fault-${suite ? "suites" : "runs"}`,{method:"POST",headers:{"Content-Type":"application/json","Idempotency-Key":pendingKey},body:serialized});
    const result=await response.json();
    if (!response.ok) throw new Error(result.detail?.code ?? "시험 요청 실패");
    current=result;pendingBody=null;pendingKey=null;sessionStorage.removeItem("eventquest.faultLabPending");rememberRun(current);draw(host);clearTimeout(poll);
    const tick=async()=>{
      try {
        const response=await fetch(`/api/test/fault-${suite ? "suites" : "runs"}/${current.id}`);
        if (!response.ok) throw new Error("시험 상태 조회 실패");
        current=await response.json();if(host.isConnected) draw(host);
        if (["QUEUED","RUNNING"].includes(current.lifecycle)) poll=setTimeout(tick,800);
      } catch (failure) {error=failure.message;if(host.isConnected) draw(host);}
    };
    poll=setTimeout(tick,200);
  } catch (failure) {error=failure.message;draw(host);}
}

function resumePoll(host) {
  clearTimeout(poll);
  const tick=async()=>{
    try {
      const response=await fetch(`/api/test/fault-${current.kind === "suite" ? "suites" : "runs"}/${current.id}`);
      if(!response.ok) throw new Error("시험 상태 조회 실패");
      current=await response.json();if(host.isConnected) draw(host);
      if(["QUEUED","RUNNING"].includes(current.lifecycle)) poll=setTimeout(tick,800);
    } catch(failure) {error=failure.message;if(host.isConnected) draw(host);}
  };
  poll=setTimeout(tick,200);
}
