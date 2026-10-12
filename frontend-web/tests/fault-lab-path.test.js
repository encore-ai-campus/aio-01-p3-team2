import assert from "node:assert/strict";
import { test } from "node:test";
import { executionPathModel } from "../src/fault-lab-path.js";

const topology = {topology_id:"canonical_planning", nodes:[
  {node_id:"planner",observed_events:["planner.started"]},
  {node_id:"supervisor",observed_events:["agent.recovery_decided"]},
]};

test("pinned observed path preserves injection detection and repeated attempts", () => {
  const rows=[
    {sequence:1,node_id:"planner",kind:"execution",event:"planner.started"},
    {sequence:2,node_id:"planner",kind:"injection"},
    {sequence:3,node_id:"planner",kind:"detection"},
    {sequence:4,node_id:"supervisor",kind:"execution",event:"agent.recovery_decided"},
    {sequence:5,node_id:"planner",kind:"execution",event:"planner.started"},
  ];
  const model=executionPathModel(topology,{path_schema_version:1,path_topology:topology,execution_path:rows,trace:[{event:"wrong.order"}]});
  assert.equal(model.legacy,false);
  assert.deepEqual(model.steps,rows);
  assert.deepEqual(model.transitions.map(e=>[e.from,e.to]),[["planner","planner"],["planner","planner"],["planner","supervisor"],["supervisor","planner"]]);
});

test("unmapped events break the graph connection without disappearing from the timeline", () => {
  const model=executionPathModel(topology,{path_schema_version:1,path_topology:topology,execution_path:[{node_id:"planner"},{node_id:"missing"},{node_id:"supervisor"}]});
  assert.equal(model.steps.length,3);
  assert.equal(model.steps[1].node_id,null);
  assert.deepEqual(model.transitions,[]);
});

test("historical reports remain explicitly marked as legacy", () => {
  const model=executionPathModel(topology,{trace:[{event:"planner.started"},{event:"unmapped"},{event:"agent.recovery_decided"}]});
  assert.equal(model.legacy,true);
  assert.equal(model.steps[1].node_id,null);
  assert.deepEqual(model.transitions,[]);
});
