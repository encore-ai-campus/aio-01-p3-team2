import { expect, test } from "@playwright/test";

// Synthetic transport fixture: backend causal ordering is verified separately in Python.
const nodes=["planner","supervisor","approval","safe_stop"].map((id,index)=>({
  node_id:id,label:id,kind:id==="planner"?"agent":"process",target_ids:id==="planner"?["planner.response"]:[],observed_events:[],
  position:{x:24+index*300,y:64,width:280,height:68},
}));
const topology={topology_id:"canonical_planning",route:"canonical_planning",version:"pinned-test-1",nodes,edges:[],layout:{width:1240,height:200,lanes:[]}};
const scenario={scenario_id:"planner_output_invalid",fault_id:"planner_output_invalid",safe_message:"기획 응답 오류",code:"ERR-CONTRACT-PLANNER-001",target_id:"planner.response",route:"canonical_planning",readiness:"ready",patterns:["once"],profiles:["current_active"]};
const result={scenario,verdict:"PASS",recovered:true,actual_terminal:"WAITING_APPROVAL",checks:[],trace:[],path_schema_version:1,path_topology:topology,execution_path:[
  {sequence:1,node_id:"planner",kind:"execution",event:"planner.started",status:"STARTED"},
  {sequence:2,node_id:"planner",kind:"injection",call_index:1,status:"INJECTED"},
  {sequence:3,node_id:"planner",kind:"detection",status:"ERROR"},
  {sequence:4,node_id:"supervisor",kind:"execution",event:"agent.recovery_decided",status:"RETRY"},
  {sequence:5,node_id:"planner",kind:"execution",event:"planner.started",status:"STARTED"},
  {sequence:6,node_id:"approval",kind:"execution",event:"approval.pending",status:"WAITING_APPROVAL"},
]};

for (const mobile of [false,true]) {
  test(`observed fault path survives polling and reload ${mobile?"mobile":"desktop"}`, async ({page})=>{
    if(mobile) await page.setViewportSize({width:390,height:844});
    await page.emulateMedia({reducedMotion:"reduce"});
    let posts=0, polls=0;
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.route("**/api/**",async route=>{
      const url=new URL(route.request().url());
      let body=[];
      if(url.pathname.endsWith("fault-status")) body={environment:"isolated",database:"test",state:"READY"};
      if(url.pathname.endsWith("fault-catalog")) body={version:"new-test-2",topologies:[{...topology,version:"new-test-2"}],targets:[{target_id:"planner.response",label:"응답 검사",supported_fault_ids:[scenario.fault_id]}],scenarios:[scenario]};
      if(url.pathname.endsWith("fault-runs") && route.request().method()==="POST") {
        posts++;
        expect(route.request().postDataJSON()).toEqual({scenario_id:scenario.scenario_id,pattern:"once",first_n:1,profile:"current_active"});
        body={id:"observed-run",kind:"run",lifecycle:"QUEUED",results:[]};
      }
      if(url.pathname.endsWith("fault-runs/observed-run")) body={id:"observed-run",kind:"run",lifecycle:++polls===1?"RUNNING":"COMPLETED",verdict:"PASS",results:polls===1?[]:[result]};
      await route.fulfill({json:body});
    });
    const open=async()=>{await page.goto("/");await page.locator(".login-submit").click();await page.getByRole("button",{name:"복구 시연",exact:true}).click();};
    await open();
    await page.getByRole("button",{name:"선택한 오류 실행",exact:true}).click();
    await expect(page.getByRole("button",{name:"선택한 오류 실행",exact:true})).toBeDisabled();
    await expect(page.locator("#lab-report")).toContainText("COMPLETED");
    await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
    await expect(page.locator(".lab-timeline li")).toHaveCount(6);
    await expect(page.locator(".lab-timeline li").nth(1)).toContainText("오류 주입");
    await expect(page.locator(".lab-timeline li").nth(2)).toContainText("실제 오류 검출");
    await expect(page.locator(".fault-node.fault-injected")).toHaveCount(1);
    await expect(page.locator(".fault-node.fault-detected")).toHaveCount(1);
    await expect(page.locator('[data-edge-from="supervisor"][data-edge-to="planner"].trace-visited')).toHaveCount(1);
    await expect(page.locator("#fault-lab")).toContainText("pinned-test-1");
    await page.screenshot({path:`test-results/path-${mobile?"mobile":"desktop"}.png`,fullPage:true});
    await open();
    await expect(page.locator("#lab-report")).toContainText("COMPLETED");
    await page.getByRole("button",{name:"실행 경로 보기",exact:true}).click();
    await expect(page.locator(".lab-timeline li")).toHaveCount(6);
    expect(posts).toBe(1);
    expect(errors).toEqual([]);
  });
}
