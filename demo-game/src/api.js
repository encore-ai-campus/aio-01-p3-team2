const BASE = "/game-api";

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    cache: "no-store",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.message || `요청 실패 (${response.status})`);
    error.code = payload.error_code || "HTTP_ERROR";
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function postIdempotent(path, payload) {
  const options = { method: "POST", body: JSON.stringify(payload) };
  try {
    return await request(path, options);
  } catch (error) {
    if (error.status && error.status < 500) throw error;
    try {
      return await request("/commands/" + encodeURIComponent(payload.request_id));
    } catch (lookupError) {
      if (lookupError.status !== 404) throw error;
      return request(path, options);
    }
  }
}

export const gameApi = {
  state: () => request("/state"),
  catalog: (kind) => request(`/catalog/${encodeURIComponent(kind)}`),
  previewEvent: (taskId) => request(`/sources/${encodeURIComponent(taskId)}`),
  applyEvent: (taskId, resultVersion = 1) => request("/events/apply", {
    method: "POST",
    body: JSON.stringify({ request_id: crypto.randomUUID(), task_id: taskId, result_version: resultVersion }),
  }),
  act: (commandType, contentId, runtimeEventId = null, stageMode = null, dungeonMode = null) => postIdempotent("/actions", {
      request_id: crypto.randomUUID(),
      command_type: commandType,
      content_id: contentId,
      runtime_event_id: runtimeEventId,
      ...(stageMode ? { stage_mode: stageMode } : {}),
      ...(dungeonMode ? { dungeon_mode: dungeonMode } : {}),
  }),
  contentAt: (kind, order) => request(`/catalog/${encodeURIComponent(kind)}/${order}`),
  inventory: (limit = 30) => request(`/inventory?limit=${limit}`),
  shopOffers: () => request("/shop/offers"),
  activateLocalSpecialDungeon: () => request("/demo-fixtures/special-dungeon", { method: "POST", body: "{}" }),
  purchaseSkin: (offerId) => postIdempotent("/shop/purchases", {
    request_id: crypto.randomUUID(), offer_id: offerId,
  }),
  equipSkin: (skinId) => postIdempotent("/skins/equip", {
    request_id: crypto.randomUUID(), skin_id: skinId,
  }),
  grow: (growthActionId) => postIdempotent("/growth-actions", {
    request_id: crypto.randomUUID(), growth_action_id: growthActionId,
  }),
  progress: (eventId) => request(`/events/${encodeURIComponent(eventId)}/progress`),
  event: (eventId) => request(`/events/${encodeURIComponent(eventId)}`),
};
