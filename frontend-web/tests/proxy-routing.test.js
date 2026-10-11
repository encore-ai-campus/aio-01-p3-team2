import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createServer as createViteServer } from "vite";
import { test } from "node:test";

test("shared frontend isolates test traffic and never falls back to ordinary API", async () => {
  const requests = { ordinary: [], fault: [] };
  const upstream = async (name) => {
    const server = createServer((req, res) => {
      requests[name].push({ path: req.url, method: req.method });
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ backend: name }));
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    return server;
  };
  const ordinary = await upstream("ordinary"), fault = await upstream("fault");
  const before = [process.env.EVENTQUEST_API_TARGET, process.env.EVENTQUEST_FAULT_API_TARGET];
  let vite;
  try {
    process.env.EVENTQUEST_API_TARGET = `http://127.0.0.1:${ordinary.address().port}`;
    process.env.EVENTQUEST_FAULT_API_TARGET = `http://127.0.0.1:${fault.address().port}`;
    vite = await createViteServer({ server: { host: "127.0.0.1", port: 0 }, logLevel: "silent" });
    await vite.listen();
    const base = `http://127.0.0.1:${vite.httpServer.address().port}`;
    for (const path of ["/api/v1/recovery-policies", "/api/game/state", "/api/testing"]) {
      assert.equal((await (await fetch(base + path)).json()).backend, "ordinary");
    }
    for (const path of ["/api/test/fault-status", "/api/test/fault-runs"]) {
      assert.equal((await (await fetch(base + path, { method: "POST" })).json()).backend, "fault");
    }
    await new Promise(resolve => fault.close(resolve));
    assert.equal((await fetch(base + "/api/test/fault-catalog")).status, 500);
    assert.equal(requests.ordinary.length, 3, "test outage must not reach ordinary backend");
    assert.equal((await (await fetch(base + "/api/v1/recovery-policies")).json()).backend, "ordinary");
    assert.equal(requests.fault.length, 2);
  } finally {
    await vite?.close();
    ordinary.closeAllConnections(); fault.closeAllConnections();
    await new Promise(resolve => ordinary.close(resolve));
    if (fault.listening) await new Promise(resolve => fault.close(resolve));
    for (const [i, key] of ["EVENTQUEST_API_TARGET", "EVENTQUEST_FAULT_API_TARGET"].entries()) {
      if (before[i] === undefined) delete process.env[key]; else process.env[key] = before[i];
    }
  }
});
