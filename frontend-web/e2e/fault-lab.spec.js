import { expect, test } from "@playwright/test";

test("static topology, scoped selection and actual recovery report in Edge", async ({page}) => {
  const errors=[];
  page.on("pageerror", (e)=>errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator(".fault-node")).toHaveCount(11);
  await expect(page.getByRole("heading",{name:/전체 오류 카탈로그 · 28개/})).toBeVisible();
  await page.locator('[data-fault-node="planner"]').click();
  await page.locator("#lab-target").selectOption("planner.response");
  await expect(page.locator(".fault-selection")).toContainText("기획안 작성 → 기획 Agent 응답 검사");
  const positions=await page.locator(".fault-topology foreignObject").evaluateAll(nodes=>nodes.map(n=>[n.getAttribute("x"),n.getAttribute("y")]));
  await page.locator("#lab-scenario").selectOption("planner_output_invalid");
  await page.locator("#lab-profile").selectOption("retry_allowed");
  await page.locator("#lab-run").click();
  await expect(page.getByRole("heading",{name:"시험 COMPLETED · PASS"})).toBeVisible({timeout:45000});
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
  await page.screenshot({path:"../docs/ik/ux-evidence/fault-lab-desktop.png",fullPage:true});
  await page.getByRole("button",{name:"실행 경로 숨기기",exact:true}).click();
  let posts=0;
  page.on("request",request=>{if(request.method()==="POST" && request.url().includes("/api/test/")) posts++;});
  await page.locator("#lab-history").selectOption({index:1});
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
