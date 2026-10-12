import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { executionPathModel, executionStepLabel } from "../src/fault-lab-path.js";

const cases=[
  {scenario:"planner_output_invalid",node:"planner",target:"planner.response",pattern:"once"},
  {scenario:"planner_explanation_mismatch",node:"critic",target:"critic.response",pattern:"once",revalidation:true},
  {scenario:"mcp_timeout",node:"mcp",target:"mcp.get_event_capabilities.call",pattern:"once"},
  {scenario:"mcp_game_rules_error",node:"mcp",target:"mcp.get_game_rules.call",pattern:"once",fallback:true},
  {scenario:"planner_output_invalid",node:"planner",target:"planner.response",pattern:"always",stopped:true},
];

for (const scenario of ["planner_output_invalid", "planner_explanation_mismatch"]) {
  test(`merged custom brief recovery ${scenario}`, async({page,request},testInfo)=>{
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.goto("/");
    await page.locator(".login-submit").click();
    await page.locator('button[data-view="create"]').click();
    await page.locator('[name="objective"]').fill("신규 유저가 달빛 던전의 공간을 탐험하도록 돕는다");
    await page.locator('[name="execution_mode"]').selectOption(scenario);
    const submitted=page.waitForResponse(response=>response.url().endsWith("/api/test/fault-runs") && response.request().method()==="POST");
    await page.locator('#brief-form button[type="submit"]').click();
    const accepted=await submitted;
    expect(accepted.status()).toBe(202);
    const id=(await accepted.json()).id;
    await expect(page.locator(".planning-demo-verdict")).toContainText("PASS",{timeout:150000});
    const record=await (await request.get(`/api/test/fault-runs/${id}`)).json();
    await writeFile(testInfo.outputPath("actual-report.json"),JSON.stringify(record,null,2));
    const result=record.results[0];
    expect(result.verdict).toBe("PASS");
    expect(result.workflow.current_status).toBe("WAITING_APPROVAL");
    expect(result.workflow.brief.objective).toBe(record.request.brief.objective);
    expect(result.workflow.brief.rewards.length).toBeGreaterThan(0);
    expect(result.recovery_comparison.kind).toBe(scenario==="planner_output_invalid"?"retry":"plan_revision");
    const events=result.workflow.trace.map(step=>step.event);
    expect(events.indexOf("storage.result_persisted")).toBeGreaterThan(events.indexOf("final_consistency.checked"));
    expect(events.indexOf("approval.pending")).toBeGreaterThan(events.indexOf("storage.result_persisted"));
    expect(events.filter(event=>event==="storage.result_persisted")).toHaveLength(1);
    expect(events).not.toContain("planning.record_persisted");
    await expect(page.locator(".workflow-board")).toContainText("기획 결과·Trace 저장 완료");
    expect(errors).toEqual([]);
  });
}

for(const item of cases) {
  test(`real isolated ${item.scenario} ${item.pattern}`,async({page,request},testInfo)=>{
    const environment=await (await request.get("/api/test/fault-status")).json();
    expect(environment).toMatchObject({environment:"isolated",state:"CLEAN"});
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    await page.emulateMedia({reducedMotion:"reduce"});
    await page.goto("/");
    await page.locator(".login-submit").click();
    await page.getByRole("button",{name:"복구 시연",exact:true}).click();
    await page.locator(`[data-fault-node="${item.node}"]`).click();
    await page.locator("#lab-target").selectOption(item.target);
    await page.locator("#lab-scenario").selectOption(item.scenario);
    await page.locator("#lab-pattern").selectOption(item.pattern);
    await page.locator("#lab-profile").selectOption("retry_allowed");
    const submitted=page.waitForResponse(response=>response.url().endsWith("/api/test/fault-runs") && response.request().method()==="POST");
    await page.getByRole("button",{name:"선택한 오류 실행",exact:true}).click();
    const accepted=await submitted;
    expect(accepted.status()).toBe(202);
    const runId=(await accepted.json()).id;
    await expect(page.locator("#lab-report .panel-heading h3")).toContainText(/COMPLETED|ABORTED/,{timeout:150000});
    const record=await (await request.get(`/api/test/fault-runs/${runId}`)).json();
    await writeFile(testInfo.outputPath("actual-report.json"),JSON.stringify(record,null,2));
    expect(record.lifecycle,JSON.stringify(record)).toBe("COMPLETED");
    const result=record.results[0];
    expect(result.verdict,JSON.stringify(result.checks.filter(check=>!check.passed))).toBe("PASS");
    expect(result.cleanup_ok).toBe(true);
    expect(result.warnings).toEqual([]);
    expect(result.llm_external_calls).toBe(0);
    expect(result.checks.find(check=>check.name==="runtime_unchanged").passed).toBe(true);
    const steps=result.execution_path;
    const injection=steps.findIndex(step=>step.kind==="injection");
    const detection=steps.findIndex(step=>step.kind==="detection");
    const recovery=steps.findIndex(step=>step.event?.endsWith(".recovery_decided"));
    expect(injection).toBeGreaterThanOrEqual(0);
    expect(detection).toBeGreaterThan(injection);
    expect(recovery).toBeGreaterThan(detection);
    expect(steps.filter(step=>step.kind==="injection").every(step=>step.target_id===item.target)).toBe(true);
    if(item.revalidation) {
      expect(result.recovery_comparison.kind).toBe("plan_revision");
      expect(result.recovery_comparison.before).toBeTruthy();
      expect(result.recovery_comparison.after).toBeTruthy();
      const validations=steps.flatMap((step,index)=>step.event==="rule_balance.validation_completed"?[index]:[]);
      const revisedPlanner=steps.findIndex((step,index)=>index>recovery && step.event==="planner.started");
      expect(result.plan_revisions).toBeGreaterThan(0);
      expect(validations.length).toBeGreaterThanOrEqual(2);
      expect(validations[0]).toBeLessThan(injection);
      expect(revisedPlanner).toBeGreaterThan(recovery);
      expect(validations.at(-1)).toBeGreaterThan(revisedPlanner);
      expect(steps[validations.at(-1)].phase).toBe("revalidation");
    }
    if(item.fallback) {
      expect(result.recovery_comparison.kind).toBe("fallback");
      expect(result.fallback_calls).toBe(1);
      expect(steps.some(step=>step.event==="mcp.recovery_decided" && step.status==="TOOL_FALLBACK")).toBe(true);
    }
    expect(result.actual_terminal).toBe(item.stopped?"SAFE_STOP":"WAITING_APPROVAL");
    expect(steps.some(step=>step.node_id===(item.stopped?"safe_stop":"approval"))).toBe(true);
    const saved=steps.findIndex(step=>step.event==="storage.result_persisted");
    expect(saved).toBeGreaterThanOrEqual(0);
    expect(steps[saved].node_id).toBe("store");
    if(item.stopped) expect(steps.at(-1).node_id).toBe("store");
    else {
      expect(saved).toBeGreaterThan(steps.findIndex(step=>step.event==="final_consistency.checked"));
      expect(steps.at(-1).node_id).toBe("approval");
      expect(steps.at(-1).event).toBe("approval.pending");
    }
    if(item.stopped) expect(steps.some(step=>step.event==="approval.pending")).toBe(false);
    await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
    const rows=page.locator(".lab-timeline li");
    await expect(rows).toHaveCount(steps.length);
    for(let index=0;index<steps.length;index++) await expect(rows.nth(index)).toContainText(`${steps[index].sequence}. ${executionStepLabel(steps[index])}`);
    if(item.revalidation) await expect(page.locator(".lab-timeline")).toContainText("수정 후 게임 규칙·보상 재검증");
    const model=executionPathModel(result.path_topology,result);
    expect(model.steps.filter(step=>!step.node_id)).toEqual([]);
    for(const transition of model.transitions) await expect(page.locator(`[data-edge-from="${transition.from}"][data-edge-to="${transition.to}"].trace-visited`)).toHaveCount(1);
    await expect(page.locator(".fault-node.fault-injected")).toHaveCount(1);
    await expect(page.locator(".fault-node.fault-detected")).toHaveCount(1);
    expect(await rows.nth(injection).evaluate(row=>row.offsetTop>=row.parentElement.scrollTop && row.offsetTop+row.clientHeight<=row.parentElement.scrollTop+row.parentElement.clientHeight)).toBe(true);
    await page.screenshot({path:testInfo.outputPath("desktop.png"),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:testInfo.outputPath("mobile.png"),fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    let repeatedPosts=0;
    page.on("request",req=>{if(req.method()==="POST" && req.url().includes("/api/test/")) repeatedPosts++;});
    await page.reload();
    await page.locator(".login-submit").click();
    await page.getByRole("button",{name:"복구 시연",exact:true}).click();
    await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
    await expect(page.locator(".lab-timeline li")).toHaveCount(steps.length);
    const restored=await (await request.get(`/api/test/fault-runs/${runId}`)).json();
    expect(restored.results[0].execution_path).toEqual(steps);
    expect(repeatedPosts).toBe(0);
    expect(errors).toEqual([]);
    expect((await (await request.get("/api/test/fault-status")).json()).state).toBe("CLEAN");
    console.log(`${item.scenario}/${item.pattern}: ${runId} · ${steps.length} path events · ${result.actual_terminal}`);
  });
}

test("saved real recovery animation follows every observed event",async({page,request},testInfo)=>{
  const runId=process.env.EVENTQUEST_PATH_SAVED_RUN_ID;
  test.skip(!runId,"Set EVENTQUEST_PATH_SAVED_RUN_ID to an actual completed isolated run.");
  const record=await (await request.get(`/api/test/fault-runs/${runId}`)).json();
  const steps=record.results[0].execution_path;
  expect(record.lifecycle).toBe("COMPLETED");
  expect(steps.length).toBeGreaterThan(0);
  let posts=0;
  page.on("request",req=>{if(req.method()==="POST") posts++;});
  await page.addInitScript(id=>sessionStorage.setItem("eventquest.faultLabRun",JSON.stringify({kind:"run",id})),runId);
  await page.goto("/");
  await page.locator(".login-submit").click();
  await page.getByRole("button",{name:"복구 시연",exact:true}).click();
  await expect(page.locator("#lab-report")).toContainText(runId);
  await page.evaluate(()=>{
    window.pathPlaybackEvidence=[];
    new MutationObserver(()=>{
      const row=document.querySelector(".lab-timeline li.current");
      if(!row) return;
      const sequence=Number(row.dataset.pathSequence);
      if(window.pathPlaybackEvidence.some(step=>step.sequence===sequence)) return;
      window.pathPlaybackEvidence.push({sequence,node_id:document.querySelector(".fault-node.trace-current")?.dataset.faultNode,
        injected:[...document.querySelectorAll(".fault-node.fault-injected")].map(n=>n.dataset.faultNode),
        detected:[...document.querySelectorAll(".fault-node.fault-detected")].map(n=>n.dataset.faultNode)});
    }).observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});
  });
  await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
  await expect(page.locator(".lab-path-playback")).toContainText("표시 완료",{timeout:20000});
  const actual=await page.evaluate(()=>window.pathPlaybackEvidence);
  await writeFile(testInfo.outputPath("animation-observations.json"),JSON.stringify({runId,actual},null,2));
  expect(actual.map(step=>[step.sequence,step.node_id])).toEqual(steps.map(step=>[step.sequence,step.node_id]));
  const injection=steps.findIndex(step=>step.kind==="injection");
  const detection=steps.findIndex(step=>step.kind==="detection");
  expect(actual[injection-1].injected).toEqual([]);
  expect(actual[injection].injected).toContain(steps[injection].node_id);
  expect(actual[detection-1].detected).toEqual([]);
  expect(actual[detection].detected).toContain(steps[detection].node_id);
  expect(posts).toBe(0);
  await page.screenshot({path:testInfo.outputPath("animation-completed.png"),fullPage:true});
});
