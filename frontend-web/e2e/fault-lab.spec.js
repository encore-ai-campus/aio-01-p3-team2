import { expect, test } from "@playwright/test";

test("actual live experiment reports match saved workflow evidence without replay",async({page,request})=>{
  const ids=(process.env.LIVE_EXPERIMENT_RUN_IDS ?? "").split(",").filter(Boolean);
  test.skip(!ids.length,"Set LIVE_EXPERIMENT_RUN_IDS to genuine completed live experiment IDs.");
  let posts=0;
  const errors=[];
  page.on("request",r=>{if(r.method()==="POST")posts++;});
  page.on("pageerror",e=>errors.push(e.message));
  await page.goto("/");
  for(const id of ids){
    const response=await request.get(`/api/test/fault-runs/${id}`);
    expect(response.ok()).toBeTruthy();
    const record=await response.json();
    expect(record.lifecycle).toBe("COMPLETED");
    const result=record.results[0];
    expect(result.provider_mode).toBe("live");
    expect(result.verdict).toBe("PASS");
    const measured=result.provider_attempts.filter(a=>a.status==="MEASURED").length;
    expect(result.checks.find(c=>c.name==="encrypted_provider_records").passed).toBe(true);
    await page.evaluate(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),id);
    await page.reload();
    await page.getByRole("button",{name:"복구 시연",exact:true}).click();
    await expect(page.locator("#lab-report")).toContainText(id);
    await expect(page.getByLabel("시험 결과 설명")).toContainText(`사용량 확인 ${measured}회`);
    await expect(page.locator("#lab-report")).toContainText(`${result.checks.length}개 일치`);
    await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
    await expect(page.locator(".fault-node.observed").first()).toBeVisible();
    if(process.env.LIVE_EXPERIMENT_EVIDENCE_DIR && (process.env.LIVE_EXPERIMENT_CAPTURE_IDS ?? id).split(",").includes(id)){
      await page.screenshot({path:`${process.env.LIVE_EXPERIMENT_EVIDENCE_DIR}/live-${id}-desktop.png`,fullPage:true});
    }
  }
  await page.reload();
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator("#lab-report")).toContainText(ids.at(-1));
  expect(posts).toBe(0);
  expect(errors).toEqual([]);
});

test("actual live recovery evidence remains readable on mobile without replay",async({page})=>{
  const id=process.env.LIVE_EXPERIMENT_MOBILE_RUN_ID;
  test.skip(!id,"Set LIVE_EXPERIMENT_MOBILE_RUN_ID to a genuine live recovery result.");
  let posts=0;
  page.on("request",r=>{if(r.method()==="POST")posts++;});
  await page.setViewportSize({width:390,height:844});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.getByLabel("시험 결과 설명")).toContainText("오류 대응 후 승인 대기까지 진행");
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await expect(page.locator(".fault-node.fault-detected")).toHaveCount(1);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  if(process.env.LIVE_EXPERIMENT_EVIDENCE_DIR){
    await page.screenshot({path:`${process.env.LIVE_EXPERIMENT_EVIDENCE_DIR}/live-${id}-mobile.png`,fullPage:true});
  }
  expect(posts).toBe(0);
});

test("last saved record restores without posting or rerunning",async({page})=>{
  const id="e63014b4-0c81-42f8-8c67-297aa37a584c";
  let posts=0;
  const errors=[];
  page.on("request",r=>{if(r.method()==="POST")posts++;});
  page.on("pageerror",e=>errors.push(e.message));
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.getByRole("heading",{name:"시험 COMPLETED · PASS"})).toBeVisible();
  await expect(page.locator("#lab-report")).toContainText(id);
  await page.reload();
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator("#lab-report")).toContainText(id);
  expect(posts).toBe(0);
  expect(errors).toEqual([]);
});

test("live report presentation uses a synthetic UI fixture without executing LLM",async({page})=>{
  const id="11111111-1111-4111-8111-111111111111";
  let posts=0;
  page.on("request",r=>{if(r.method()==="POST")posts++;});
  const record={id,kind:"run",lifecycle:"COMPLETED",verdict:"PASS",results:[{
    provider_mode:"live",experiment_kind:"normal",verdict:"PASS",actual_terminal:"WAITING_APPROVAL",cleanup_ok:true,
    policy_ref:{version:1},provider_attempts:[{status:"MEASURED",input_tokens:10,output_tokens:5,cost:"0.00002"}],checks:[],
    scenario:{scenario_id:"normal_single",target_id:"planner.response",route:"canonical_planning"},trace:[],
  }]};
  await page.route(`**/api/test/fault-runs/${id}`,route=>route.fulfill({json:record}));
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.getByLabel("시험 결과 설명")).toContainText("오류 주입 없음");
  await expect(page.getByLabel("시험 결과 설명")).toContainText("사용량 확인 1회");
  await expect(page.getByLabel("시험 결과 설명")).toContainText("청구액 확정 아님");
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await expect(page.locator(".fault-node.fault-detected")).toHaveCount(0);
  expect(posts).toBe(0);
});

test("known trace metadata is explained separately from mapping warnings",async({page,request})=>{
  const id=process.env.FAULT_LAB_METADATA_RUN_ID;
  test.skip(!id,"Set FAULT_LAB_METADATA_RUN_ID to the completed metadata follow-up run.");
  const record=await (await request.get(`/api/test/fault-runs/${id}`)).json();
  expect(record.lifecycle).toBe("COMPLETED");
  expect(record.results[0].warnings).toEqual([]);
  expect(record.results[0].observations).toHaveLength(2);
  let posts=0;
  page.on("request",r=>{if(r.method()==="POST" && r.url().includes("/api/test/"))posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await page.getByText("보조 관측 정보 · 2개",{exact:true}).click();
  await expect(page.getByText("LLM 사용량과 결과 저장 기록입니다.",{exact:false})).toBeVisible();
  await expect(page.getByText(/^UNMAPPED_EVENT · 미매핑 관측/)).toHaveCount(0);
  await expect(page.getByLabel("시험 결과 설명")).toContainText("PASS · 기대한 정책 대응");
  expect(posts).toBe(0);
});

test("historical failed reports retain mismatch evidence without replay",async({page,request})=>{
  const id=process.env.FAULT_LAB_FAILURE_RUN_ID;
  test.skip(!id,"Set FAULT_LAB_FAILURE_RUN_ID to the preserved failed preparation suite.");
  const record=await (await request.get(`/api/test/fault-suites/${id}`)).json();
  expect(record.lifecycle).toBe("COMPLETED");
  const indices=record.results.flatMap((r,i)=>r.verdict==="FAIL" ? [i] : []);
  expect(indices).toHaveLength(3);
  let posts=0;
  page.on("request",r=>{if(r.method()==="POST" && r.url().includes("/api/test/"))posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"suite",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  for(const index of indices){
    await page.locator("#lab-result").selectOption(String(index));
    await expect(page.getByLabel("시험 결과 설명")).toContainText("FAIL · 기대 결과와 다른 항목");
    await page.getByText(/^검증 항목 · 불일치/, {exact:false}).click();
    await expect(page.locator(".fault-checks")).toContainText("기대·실제 불일치");
    await page.getByText("기대값과 실제값",{exact:true}).first().click();
    await expect(page.locator(".fault-checks pre").first()).toContainText('"expected"');
    await expect(page.locator(".fault-checks pre").first()).toContainText('"actual"');
    await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  }
  expect(posts).toBe(0);
});

test("repeated campaign outcomes distinguish retries revisions and safe stops",async({page,request})=>{
  const id=process.env.FAULT_LAB_REPEAT_RUN_ID;
  test.skip(!id,"Set FAULT_LAB_REPEAT_RUN_ID to the completed retry policy campaign.");
  const record=await (await request.get(`/api/test/fault-suites/${id}`)).json();
  expect(record.lifecycle).toBe("COMPLETED");
  expect(record.results).toHaveLength(46);
  const selected=new Set(["planner_explanation_mismatch","planner_output_invalid","llm_timeout","mcp_timeout","event_brief_mismatch"]);
  const indices=record.request.cases.flatMap((c,i)=>selected.has(c.scenario_id) && c.pattern!=="once" ? [i] : []);
  expect(indices).toHaveLength(20);
  let posts=0;
  page.on("request",r=>{if(r.method()==="POST" && r.url().includes("/api/test/"))posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"suite",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  for(const index of indices){
    const result=record.results[index];
    expect(result.verdict).toBe("PASS");
    await page.locator("#lab-result").selectOption(String(index));
    const summary=page.getByLabel("시험 결과 설명");
    const outcome=result.actual_terminal==="SAFE_STOP" ? "안전 중단" : result.actual_terminal==="FAILED" ? "작업 실패 종료 (FAILED) · 시험 판정과 별도" : "자동 복구 후 작업을 계속했습니다.";
    await expect(summary).toContainText(outcome);
    await expect(page.getByText(`재시도 ${result.retries}회 · 기획 수정 ${result.plan_revisions}회 · 대체 조회 ${result.fallback_calls}회.`,{exact:false})).toBeVisible();
    if(result.plan_revisions>0) await expect(page.getByText(`해당 경계 관측 ${result.attempts}회 · 기획 수정 후 재검증을 포함합니다.`,{exact:true})).toBeVisible();
    else await expect(page.getByText(`해당 작업의 최초 시도 1회 + 재시도 ${result.retries}회 · 전체 시도 ${result.attempts}회`,{exact:true})).toBeVisible();
    await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  }
  expect(posts).toBe(0);
});

test("all 28 campaign outcomes render actual evidence without replay in Edge",async({page,request})=>{
  const id=process.env.FAULT_LAB_BASIC_RUN_ID;
  test.skip(!id,"Set FAULT_LAB_BASIC_RUN_ID to the completed basic 28-case campaign.");
  const response=await request.get(`/api/test/fault-suites/${id}`);
  expect(response.ok()).toBeTruthy();
  const record=await response.json();
  expect(record.lifecycle).toBe("COMPLETED");
  expect(record.results).toHaveLength(28);
  const errors=[];let posts=0;
  page.on("pageerror",e=>errors.push(e.message));
  page.on("request",r=>{if(r.method()==="POST" && r.url().includes("/api/test/"))posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"suite",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  const terminals={REQUEST_BLOCKED:"입력 요청 거절",WAITING_APPROVAL:"사람 승인 대기",SAFE_STOP:"안전 중단",NEEDS_DATA:"추가 게임 데이터 필요",NEEDS_DEVELOPMENT:"지원 기능 개발 필요"};
  for(let index=0;index<record.results.length;index++){
    const actual=record.results[index];
    expect(actual.verdict).toBe("PASS");
    await page.locator("#lab-result").selectOption(String(index));
    const summary=page.getByLabel("시험 결과 설명");
    await expect(summary).toContainText(actual.scenario.code);
    await expect(summary).toContainText(actual.observed_error_code);
    await expect(summary).toContainText("주입 확인");
    await expect(summary).toContainText("PASS · 기대한 정책 대응");
    const terminal=actual.scenario.scenario_id==="supervisor_state_conflict" ? "상태 충돌 요청 거절" : terminals[actual.actual_terminal];
    if(terminal) await expect(summary).toContainText(terminal);
    await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
    const labels=await summary.locator("b").allTextContents();
    expect(labels).toEqual(["1. 주입한 오류","2. 검출 결과","3. 정책 결정","4. 최종 상태","5. 시험 판정"]);
  }
  const downloadPromise=page.waitForEvent("download");
  await page.getByRole("button",{name:"JSON 보고서 저장"}).click();
  const downloaded=await downloadPromise;
  const {readFile}=await import("node:fs/promises");
  expect(JSON.parse(await readFile(await downloaded.path(),"utf8"))).toEqual(record);
  await page.locator("#lab-result").selectOption("9");
  await page.screenshot({path: test.info().outputPath("fault-campaign-28-desktop.png"),fullPage:true});
  await page.reload();
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator("#lab-result option")).toHaveCount(29);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button",{name:"실행 경로 숨기기",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"시험 결과로 돌아가기",exact:true}).click();
  await page.screenshot({path: test.info().outputPath("fault-campaign-28-mobile.png"),fullPage:true});
  expect(posts).toBe(0);
  expect(errors).toEqual([]);
});

test("static topology, scoped selection and actual recovery report in Edge", async ({page}) => {
  const errors=[];
  page.on("pageerror", (e)=>errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator(".fault-node")).toHaveCount(10);
  await expect(page.getByRole("heading",{name:/전체 오류 카탈로그 · 26개/})).toBeVisible();
  await page.locator('[data-fault-node="planner"]').click();
  await page.locator("#lab-target").selectOption("planner.response");
  await expect(page.locator(".fault-selection")).toContainText("기획안 작성 → 기획 Agent 응답 검사");
  const positions=await page.locator(".fault-topology foreignObject").evaluateAll(nodes=>nodes.map(n=>[n.getAttribute("x"),n.getAttribute("y")]));
  await page.locator("#lab-scenario").selectOption("planner_output_invalid");
  await page.locator("#lab-profile").selectOption("retry_allowed");
  await page.locator("#lab-run").click();
  await expect(page.getByRole("heading",{name:"시험 COMPLETED · PASS"})).toBeVisible({timeout:45000});
  console.log("UI static recovery run:",await page.locator("#lab-report .panel-heading span").textContent());
  await expect(page.locator("#lab-profile")).toHaveValue("retry_allowed");
  expect(await page.locator(".fault-topology foreignObject").evaluateAll(nodes=>nodes.map(n=>[n.getAttribute("x"),n.getAttribute("y")]))).toEqual(positions);
  await expect(page.locator('[data-fault-node="planner"]')).toHaveAttribute("aria-pressed","true");
  await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  await expect(page.getByLabel("시험 결과 설명")).toContainText("PASS · 기대한 정책 대응");
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await expect(page.locator('[data-fault-node="planner"]')).toHaveClass(/fault-detected/);
  await expect(page.getByText(/최초 시도 1회 \+ 재시도 1회 · 전체 시도 2회/)).toBeVisible();
  await expect(page.locator(".fault-node.observed")).not.toHaveCount(0);
  await page.getByRole("button",{name:"시험 결과로 돌아가기",exact:true}).click();
  await expect(page.getByLabel("시험 결과 설명")).toBeInViewport();
  const download=page.waitForEvent("download");
  await page.getByRole("button",{name:"JSON 보고서 저장"}).click();
  expect((await download).suggestedFilename()).toMatch(/^fault-.*\.json$/);
  await page.screenshot({path: test.info().outputPath("fault-campaign-selection-desktop.png"),fullPage:true});
  await page.getByRole("button",{name:"실행 경로 숨기기",exact:true}).click();
  let posts=0;
  page.on("request",request=>{if(request.method()==="POST" && request.url().includes("/api/test/")) posts++;});
  await page.reload();
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.getByLabel("시험 결과 설명")).toContainText("PASS · 기대한 정책 대응");
  expect(posts).toBe(0);
  await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await page.locator('[data-fault-node="mcp"]').click();
  await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  await page.locator("#lab-advanced summary").click();
  await expect(page.locator('#lab-topology option[value="approved_runtime"]')).toHaveCount(0);
  await page.locator("#lab-topology").selectOption("legacy_task");
  await page.locator('[data-fault-node="legacy_planner"]').click();
  await expect(page.locator("#lab-scenario option")).toHaveCount(3);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(errors).toEqual([]);
});

test("accepted request is idempotent and rejects different content with same key",async({request})=>{
  const headers={"Idempotency-Key":crypto.randomUUID()};
  const body={scenario_id:"llm_execution_error",pattern:"once",first_n:1,profile:"safe_stop"};
  const first=await request.post("/api/test/fault-runs",{headers,data:body});
  expect(first.status()).toBe(202);
  const admitted=await first.json();
  console.log("UI idempotency run:",admitted.id);
  const again=await request.post("/api/test/fault-runs",{headers,data:body});
  expect(again.status()).toBe(202);
  expect((await again.json()).id).toBe(admitted.id);
  const conflict=await request.post("/api/test/fault-runs",{headers,data:{...body,scenario_id:"unknown_error"}});
  expect(conflict.status()).toBe(409);
  await expect.poll(async()=> (await (await request.get(`/api/test/fault-runs/${admitted.id}`)).json()).lifecycle,{timeout:45000}).toBe("COMPLETED");
});


test("suite exposes each case separately without replaying the run",async({page,request})=>{
  const response=await request.post("/api/test/fault-suites",{headers:{"Idempotency-Key":crypto.randomUUID()},data:{cases:[
    {scenario_id:"planner_output_invalid",pattern:"once",first_n:1,profile:"safe_stop"},
    {scenario_id:"llm_execution_error",pattern:"once",first_n:1,profile:"safe_stop"},
  ]}});
  expect(response.status()).toBe(202);
  const run=await response.json();
  console.log("UI safe-stop suite:",run.id);
  await expect.poll(async()=> (await (await request.get(`/api/test/fault-suites/${run.id}`)).json()).lifecycle,{timeout:45000}).toBe("COMPLETED");
  await page.addInitScript((id)=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"suite",id})),run.id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator("#lab-result option")).toHaveCount(3);
  await page.locator("#lab-result").selectOption("0");
  await expect(page.getByText(/선택 2 · 실행 2 · 통과 2 · 실패 0/)).toBeVisible();
  await expect(page.getByLabel("시험 결과 설명")).toContainText("안전 중단");
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await expect(page.locator(".fault-node.fault-detected")).toHaveCount(1);
  await page.locator("#lab-result").selectOption("1");
  await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  await expect(page.getByRole("heading",{name:"시험 COMPLETED · PASS"})).toBeVisible();
});


test("policy editor displays the inherited MCP rule for historical policies",async({page,request})=>{
  const state=await (await request.get("/api/v1/recovery-policies")).json();
  const source=[...state.versions].reverse().find(v=>v.status==="DRAFT") ?? state.versions.find(v=>v.status==="ACTIVE");
  const inherited=source.compatibility_mappings?.["ERR-DEP-MCP-003"];
  const expected=source.policy.routes["ERR-DEP-MCP-003"] ?? (inherited ? source.policy.routes[inherited] : state.suggested_draft.routes["ERR-DEP-MCP-003"]);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 정책",exact:true}).click();
  await expect(page.locator('[data-policy-action="ERR-DEP-MCP-003"]')).toHaveValue(expected.action);
  await expect(page.locator('[data-policy-attempts="ERR-DEP-MCP-003"]')).toHaveValue(String(expected.max_attempts));
});


test("precision preparation reports match actual special outcomes without replay",async({page,request})=>{
  const id=process.env.FAULT_LAB_PRECISION_RUN_ID;
  test.skip(!id,"Set FAULT_LAB_PRECISION_RUN_ID to a completed preparation smoke suite.");
  const response=await request.get(`/api/test/fault-suites/${id}`);
  expect(response.ok()).toBeTruthy();
  const record=await response.json();
  expect(record.lifecycle).toBe("COMPLETED");
  expect(record.results).toHaveLength(10);
  const errors=[];let posts=0;
  page.on("pageerror",e=>errors.push(e.message));
  page.on("request",r=>{if(r.method()==="POST" && r.url().includes("/api/test/"))posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"suite",id})),id);
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  const expectations=[
    "입력 요청 거절","입력 요청 거절","지원 기능 개발 필요","추가 게임 데이터 필요",
    "기획안 재작성 후 재검증","검증된 대체 게임 규칙 조회","실제 관측 원인 ERR-CONTRACT-PLANNER-001",
    "상태 충돌 요청 거절","쓰기를 재실행한 것이 아닙니다.","자동 복구 후 작업을 계속했습니다.",
  ];
  for(let index=0;index<expectations.length;index++){
    await page.locator("#lab-result").selectOption(String(index));
    await expect(page.getByLabel("시험 결과 설명")).toContainText(expectations[index]);
    await expect(page.getByLabel("시험 결과 설명")).toContainText("PASS · 기대한 정책 대응");
    await expect(page.locator(".fault-node.observed, .fault-node.fault-detected")).toHaveCount(0);
  }
  await page.locator("#lab-result").selectOption("5");
  await expect(page.getByText(/Primary 조회 2회 · 별도 콘텐츠 조회/)).toBeVisible();
  await page.locator("#lab-result").selectOption("4");
  await expect(page.getByText(/해당 경계 관측 2회 · 기획 수정 후 재검증/)).toBeVisible();
  expect(posts).toBe(0);
  expect(errors).toEqual([]);
});
