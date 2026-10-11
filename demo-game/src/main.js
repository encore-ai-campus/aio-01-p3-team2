import Phaser from "phaser";
import { BootScene } from "./scenes/BootScene.js";
import { LobbyScene } from "./scenes/LobbyScene.js";
import { DungeonScene } from "./scenes/DungeonScene.js";
import { ResultScene } from "./scenes/ResultScene.js";
import { gameApi } from "./api.js";
import starCandySkin from "../assets/skin-star-candy.webp";
import mushroomSkin from "../assets/skin-mushroom.webp";
import moonBunnySkin from "../assets/skin-moon-bunny.webp";
import "./style.css";

const $ = (selector) => document.querySelector(selector);
const state = { user: null, event: null, progress: null, inventory: null, shop: null, versions: null, catalogs: {}, mode: "lobby", busy: false, huntActive: false, huntPaused: false };
const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game-stage",
  width: 960,
  height: 540,
  backgroundColor: "#172331",
  scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
  render: { antialias: true, pixelArt: false, roundPixels: false },
  scene: [BootScene, LobbyScene, DungeonScene, ResultScene],
  fps: { target: 60, forceSetTimeOut: true },
});

function syncWorld(mode = state.mode) {
  const manager = game.scene;
  const user = state.user;
  const order = user ? (user.hunt_mode === "push" ? user.highest_stage_order + 1 : Math.max(1, user.highest_stage_order)) : 1;
  const stage = state.catalogs.stages?.find((item) => item.order === order) || {
    id: `stage_${String(order).padStart(4, "0")}`,
    order,
    name: `달빛 초원 ${order}구역`,
    monster: { name: ["초원 슬라임", "숲 고블린", "수정 박쥐", "폐광 수호자", "달빛 정령"][(order - 1) % 5], visual_slot: ((order - 1) % 5) + 1 },
  };
  manager.getScene("LobbyScene").drawWorld(mode, user, stage);
}

function returnToWorld() {
  const manager = game.scene;
  for (const key of ["ResultScene", "DungeonScene"]) {
    if (manager.isActive(key)) {
      manager.getScene(key).scene.start("LobbyScene", { mode: state.mode, user: state.user, stage: state.catalogs.stages?.find((item) => item.id === state.user?.current_hunt_stage_id) });
      return;
    }
  }
  syncWorld(state.mode);
}

let toastTimer;
function toast(message, isError = false) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 3000);
}

function itemName(id) { return state.catalogs.items?.find((item) => item.item_id === id)?.name || id; }
function activeEvent() {
  return state.event?.status === "active" ? state.event : null;
}

async function loadCatalogs() {
  const [stages, dungeons, templates, items] = await Promise.all([
    gameApi.catalog("stages"), gameApi.catalog("dungeons"), gameApi.catalog("special_dungeon_templates"), gameApi.catalog("items"),
  ]);
  state.catalogs = { stages: stages.items, dungeons: dungeons.items, templates: templates.items, items: items.items };
}

async function refresh({ quiet = false } = {}) {
  try {
    if (!state.catalogs.stages) await loadCatalogs();
    const [snapshot, inventory, shop] = await Promise.all([gameApi.state(), gameApi.inventory(), gameApi.shopOffers()]);
    state.user = snapshot.user;
    state.inventory = inventory;
    state.shop = shop;
    state.versions = snapshot.versions;
    const chosen = snapshot.events.find((event) => event.status === "active") || snapshot.events[0] || null;
    state.event = chosen;
    state.progress = chosen ? await gameApi.progress(chosen.runtime_event_id) : null;
    render();
    if (!quiet) toast("게임 상태를 최신 기록으로 불러왔습니다.");
  } catch (error) {
    renderUnavailable(error);
    if (!quiet) toast(error.message, true);
  }
}

function renderUnavailable(error) {
  $("#player-name").textContent = "게임 연결 대기";
  $("#player-level").textContent = "–";
  $("#version-label").textContent = (state.versions?.game_data_snapshot_id || state.progress?.event?.versions?.game_data_snapshot_id || "opong-data-1.1.0") + " · " + (state.versions?.game_ruleset_version || state.progress?.event?.versions?.game_ruleset_version || "opong-rules-1.1.0");
  $("#guide-copy").textContent = error.message;
}

function render() {
  const user = state.user;
  if (!user) return;
  $("#player-name").textContent = user.display_name;
  $("#player-level").textContent = user.level;
  $("#gold-count").textContent = (user.inventory.GOLD || 0).toLocaleString("ko-KR");
  $("#gem-count").textContent = (user.inventory.GEM || 0).toLocaleString("ko-KR");
  $("#shard-count").textContent = (user.inventory.MOON_SHARD || 0).toLocaleString("ko-KR");
  $("#xp-label").textContent = user.experience_to_next_level ? `${user.experience_in_level} / ${user.experience_to_next_level} EXP` : "최고 레벨 달성";
  $("#xp-fill").style.width = user.experience_to_next_level
    ? `${Math.min(100, (user.experience_in_level / user.experience_to_next_level) * 100)}%` : "100%";
  const selected = state.event;
  const live = activeEvent();
  $("#rift-lock").textContent = live?.venue ? "개방" : "잠김";
  $(".special-nav").classList.toggle("unlocked", Boolean(live?.venue));
  if (selected) {
    $("#event-title").textContent = selected.event_name;
    $("#event-subtitle").textContent = selected.status === "active" ? "달빛의 부름이 원정대를 기다립니다" : "종료된 이벤트 기록을 조회 중입니다";
    $("#event-period").textContent = selected.end_at
      ? `${formatDate(selected.start_at)} — ${formatDate(selected.end_at)}`
      : `${formatDate(selected.start_at)} — 상시 운영`;
    $("#event-target").textContent = targetLabel(selected.target_user);
    $("#event-apply-state").textContent = selected.status === "active" ? "게임에 적용됨" : selected.status === "ended" ? "이벤트 종료" : "시작 대기";
    $("#event-apply-state").className = selected.status === "active" ? "success-value" : "muted-value";
    $("#event-status").innerHTML = `<i class="status-dot ${selected.status}"></i><span>${selected.fixture ? "로컬 게임 fixture" : `${statusLabel(selected.status)} · Task ${selected.source_task_id}`}</span>`;
    renderMissions(state.progress?.missions || []);
    renderRewards(state.progress?.rewards || []);
    renderAudit(selected, state.progress);
  } else {
    $("#event-title").textContent = "월광 균열";
    $("#event-subtitle").textContent = "기획 이벤트를 적용하면 여정이 시작됩니다";
    $("#event-period").textContent = "이벤트 미적용";
    $("#event-target").textContent = "승인된 Task 대기";
    $("#event-apply-state").textContent = "잠금";
    $("#event-status").innerHTML = '<i class="status-dot"></i><span>연결 대기</span>';
    renderMissions([]);
    renderRewards([]);
    $("#audit-panel").innerHTML = "<p>이벤트가 적용되면 검증 기록이 표시됩니다.</p>";
  }
  $("#version-label").textContent = (state.versions?.game_data_snapshot_id || state.progress?.event?.versions?.game_data_snapshot_id || "opong-data-1.1.0") + " · " + (state.versions?.game_ruleset_version || state.progress?.event?.versions?.game_ruleset_version || "opong-rules-1.1.0");
  $("#guide-copy").textContent = live?.venue
    ? "기획안에서 선택한 이벤트 공간에 입장하세요. 서버가 확정한 행동만 미션과 보상에 기록됩니다."
    : "스테이지를 클리어해 영웅을 성장시키고, 승인된 이벤트로 특별 던전을 여세요.";
  renderChoices();
  syncWorld(state.mode);
}

function renderMissions(missions) {
  const root = $("#mission-list");
  const complete = missions.filter((mission) => mission.completed).length;
  $("#quest-count").textContent = `${complete} / ${missions.length}`;
  if (!missions.length) {
    root.innerHTML = '<div class="empty-quest"><span>☾</span><b>아직 등록된 임무가 없습니다</b><small>승인된 이벤트를 불러와 주세요</small></div>';
    return;
  }
  root.innerHTML = missions.map((mission) => {
    const value = Math.min(mission.current_value, mission.target);
    const percent = Math.min(100, (value / mission.target) * 100);
    const missionStatus = mission.status || (mission.completed ? "completed" : "active");
    const statusLabelText = { completed: "완료", active: "진행 중", upcoming: "예정", missed: "기간 종료" }[missionStatus];
    const actionLabels = {
      monster_kill: "월광 균열 몬스터 처치",
      dungeon_clear: "일반 던전 클리어",
      stage_clear: "스테이지 클리어",
      level_reach: "영웅 레벨 성장",
    };
    return `<article class="mission ${missionStatus}">
      <div class="mission-mark">${missionStatus === "completed" ? "✓" : missionStatus === "active" ? "✧" : "·"}</div>
      <div class="mission-content"><div class="mission-title-row"><b>${escapeHtml(mission.name)}</b><span>${mission.active_during_event ? "이벤트 기간 중 첫 클리어" : `DAY ${mission.day}`}</span></div>
      <small class="mission-mapping">게임 연결: ${escapeHtml(actionLabels[mission.source_mission_type] || mission.action_type)}</small>
      <div class="mission-progress-row"><span>${value} <i>/ ${mission.target}</i></span><b>${statusLabelText}</b></div>
      <div class="progress-track"><i style="width:${percent}%"></i></div></div></article>`;
  }).join("");
}

function renderRewards(rewards) {
  const root = $("#reward-list");
  if (!rewards.length) {
    root.innerHTML = '<span class="empty-reward">미션 완료 후 보상이 여기에 기록됩니다</span>';
    return;
  }
  root.innerHTML = rewards.map((reward) => `<div class="reward-entry"><span class="reward-glyph">✧</span><b>${escapeHtml(itemName(reward.item_id))}</b><strong>+${reward.quantity}</strong><small>${escapeHtml(reward.item_id)}</small></div>`).join("");
}

function renderAudit(event, progress) {
  const attempts = (progress?.attempts || []).map((attempt) => `<li><b>${attempt.result === "succeeded" ? "성공" : "실패"}</b> · ${escapeHtml(attempt.command_type)} / ${escapeHtml(attempt.content_id)}<small>Attempt ${escapeHtml(attempt.attempt_id)} · ${escapeHtml(attempt.reason_code)}</small></li>`).join("");
  const actions = (progress?.actions || []).map((action) => `<li><b>${escapeHtml(action.action_type)}</b> · ${escapeHtml(action.content_id)}<small>Action ${escapeHtml(action.action_id)} · ${escapeHtml(action.subtype)}</small></li>`).join("");
  $("#audit-panel").innerHTML = `<dl>
    <dt>Runtime Event</dt><dd>${event.runtime_event_id}</dd>
    <dt>${event.fixture ? "Fixture" : "Source Task"}</dt><dd>${event.fixture ? "승인 Workflow와 독립된 로컬 게임 데이터" : `${event.source_task_id} · v${event.result_version}`}</dd>
    <dt>Payload Digest</dt><dd>${event.payload_digest}</dd>
    <dt>이벤트 상태</dt><dd>${statusLabel(event.status)}</dd>
    <dt>기획안 데이터</dt><dd>${escapeHtml(event.source_game_data_version || "확인 불가")}</dd>
    <dt>카탈로그 / 규칙</dt><dd>${event.versions.game_data_snapshot_id} / ${event.versions.game_ruleset_version}</dd>
  </dl><div class="audit-records"><b>최근 도전</b><ul>${attempts || "<li>기록된 도전이 없습니다.</li>"}</ul><b>서버 확정 Action</b><ul>${actions || "<li>확정된 Action이 없습니다.</li>"}</ul></div>`;
}

function renderChoices() {
  const root = $("#content-choices");
  root.classList.toggle("content-screen", ["collection", "shop"].includes(state.mode));
  const live = activeEvent();
  let options = [];
  let command = "stage_challenge";
  let title = "모험 지역 선택";
  let hint = "도전을 시작할 지역을 선택하세요";
  if (state.mode === "stage") {
    options = state.catalogs.stages || [];
    title = "스테이지 원정";
    hint = "전투 승리로 경험치와 왕국 골드를 획득합니다";
  } else if (state.mode === "dungeon") {
    const order = Math.max(1, state.user.highest_dungeon_floor || 0);
    const nextOrder = (state.user.highest_dungeon_floor || 0) + 1;
    const currentFloor = state.user.dungeon_hunt_mode === "push" ? nextOrder : order;
    command = "dungeon_challenge";
    title = "일반 던전";
    hint = `최고 ${state.user.highest_dungeon_floor || 0}층 · ${state.user.dungeon_success_count || 0}회 클리어 · 제한 없는 폐광 원정`;
  } else if (state.mode === "special") {
    const templateId = live?.venue?.template_id;
    const template = state.catalogs.templates?.find((row) => row.template_id === templateId);
    const configuredSpaceIds = live?.venue?.space_ids;
    options = (template?.spaces || template?.floors || []).filter((space) =>
      !Array.isArray(configuredSpaceIds) || configuredSpaceIds.includes(space.id),
    );
    command = "special_dungeon_challenge";
    title = live?.event_name || "월광 균열";
    hint = live ? `이 이벤트에 열린 공간 ${options.length}곳 · 순서와 관계없이 도전 가능` : "승인 이벤트를 게임에 적용해야 입장할 수 있습니다";
  }
  $("#dock-title").textContent = title;
  $("#dock-hint").textContent = hint;
  if (state.mode === "lobby") {
    root.innerHTML = `<button class="content-card" data-mode="stage"><span>⚔</span><b>초원 원정</b><small>무한 자동 사냥 · 현재 ${state.user?.current_hunt_stage_order || 1}구역</small><i>→</i></button><button class="content-card" data-mode="dungeon"><span>⬟</span><b>별무리 폐광</b><small>무한 층 도전 · 현재 ${state.user?.current_dungeon_floor || 1}층</small><i>→</i></button><button class="content-card" data-mode="special"><span>✧</span><b>월광 균열</b><small>승인 이벤트 특별 공간</small><i>→</i></button><button class="content-card" data-mode="growth"><span>⚒</span><b>원정대 성장</b><small>레벨 ${state.user?.level || 1} · 공격력과 최대 HP 강화</small><i>성장 →</i></button><button class="content-card" data-mode="collection"><span>▣</span><b>원정대 보관함</b><small>재화 · 보유 스킨 · 획득 기록</small><i>열기 →</i></button><button class="content-card" data-mode="shop"><span>✿</span><b>꾸미기 상점</b><small>별빛 보석으로 스킨 교환</small><i>열기 →</i></button>`;
    root.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
    return;
  }
  if (state.mode === "growth") {
    const user = state.user;
    $("#dock-title").textContent = "영웅 능력치 강화";
    $("#dock-hint").textContent = "재화를 사용해 다음 전투에 적용할 능력치를 올리세요";
    const upgrades = Object.entries(user.growth_options || {});
    const cards = upgrades.map(([id, action]) => {
      const gold = Number(user.inventory?.GOLD || 0);
      const shards = Number(user.inventory?.MOON_SHARD || 0);
      const shardCost = Number(action.next_moon_shard_cost || 0);
      const canAfford = action.next_cost !== null && gold >= action.next_cost && shards >= shardCost;
      const currentStat = action.stat === "attack" ? user.stats.attack : user.stats.max_hp;
      const statName = action.stat === "attack" ? "공격력" : "최대 HP";
      const costLabel = action.next_cost === null
        ? "강화 한도 도달"
        : `${Number(action.next_cost).toLocaleString("ko-KR")} GOLD${shardCost ? ` · ${shardCost} 월광 파편` : ""}`;
      return `<article class="growth-option"><div class="growth-option-title"><span>✦</span><div><b>${escapeHtml(action.name)}</b><small>RANK ${action.level}</small></div></div><div class="growth-stat-change"><span>${statName}</span><b>${Number(currentStat).toLocaleString("ko-KR")} <i>→ ${action.next_cost === null ? "MAX" : Number(currentStat + action.amount).toLocaleString("ko-KR")}</i></b></div><p>강화 1회당 ${statName} +${action.amount}</p><div class="growth-option-footer"><small>다음 강화 비용 · ${costLabel}</small><button class="button ${canAfford ? "button-bright" : ""}" data-growth="${escapeAttr(id)}" ${canAfford ? "" : "disabled"}>${action.next_cost === null ? "최대 강화" : canAfford ? "강화하기" : "재화 부족"}</button></div></article>`;
    }).join("");
    root.innerHTML = `<div class="screen-heading"><div><b>원정대 성장</b><small>전투 보상으로 모은 재화를 사용해 영웅의 능력치를 높이세요</small></div><div class="screen-balance">✧ ${Number(user.inventory?.GOLD || 0).toLocaleString("ko-KR")} GOLD<br/>◈ ${Number(user.inventory?.MOON_SHARD || 0).toLocaleString("ko-KR")} 월광 파편</div></div><div class="growth-overview"><div class="growth-overview-stat"><small>현재 레벨</small><b>Lv. ${user.level}</b></div><div class="growth-overview-stat"><small>공격력</small><b>${Number(user.stats.attack).toLocaleString("ko-KR")}</b></div><div class="growth-overview-stat"><small>최대 HP</small><b>${Number(user.stats.max_hp).toLocaleString("ko-KR")}</b></div></div><div class="growth-options">${cards}</div>`;
    root.querySelectorAll("[data-growth]").forEach((button) => button.addEventListener("click", () => grow(button.dataset.growth)));
    return;
  }
  if (state.mode === "shop") {
    const offers = state.shop?.offers || [];
    root.innerHTML = `<div class="screen-heading"><div><b>왕국 꾸미기 상점</b><small>첫 클리어로 모은 별빛 보석을 귀여운 스킨으로 교환하세요</small></div><div class="screen-heading-actions"><span class="screen-balance">◆ ${Number(state.user?.inventory?.GEM || 0).toLocaleString("ko-KR")} GEM</span><button class="button screen-home-button" data-mode="lobby" aria-label="왕국 로비로 돌아가기">⌂ 로비</button></div></div><div class="skin-grid">${offers.map((offer) => {
      const skin = offer.item;
      const canBuy = !offer.owned && Number(state.user?.inventory?.[offer.price_item_id] || 0) >= offer.price;
      const gems = Number(state.user?.inventory?.[offer.price_item_id] || 0);
      return `<article class="skin-card ${offer.owned ? "owned" : ""}" data-skin="${escapeAttr(offer.item_id)}"><div class="skin-preview ${skinClass(offer.item_id)}"><img src="${skinImage(offer.item_id)}" alt="${escapeAttr(skin.name)} 착용 모습"/><span>✦</span></div><h3>${escapeHtml(skin.name)}</h3><p>${escapeHtml(skin.style || "원정대를 위한 귀여운 외형")}</p><div class="skin-card-footer">${offer.owned ? `<b class="owned-label">보유 중</b>` : `<button class="button ${canBuy ? "button-bright" : ""}" data-buy="${escapeAttr(offer.offer_id)}" ${canBuy ? "" : "disabled"}>${canBuy ? `◆ ${offer.price} GEM 교환` : `GEM 부족 (${gems}/${offer.price})`}</button>`}</div></article>`;
    }).join("")}</div>`;
    root.querySelectorAll("[data-buy]").forEach((button) => button.addEventListener("click", () => purchaseSkin(button.dataset.buy)));
    root.querySelector("[data-mode=lobby]").addEventListener("click", () => setMode("lobby"));
    return;
  }
  if (state.mode === "collection") {
    const skins = state.inventory?.skins || [];
    const ledger = state.inventory?.ledger || [];
    root.innerHTML = `<div class="screen-heading"><div><b>원정대 보관함</b><small>보유 재화와 스킨, 최근 획득·소비 기록</small></div><div class="screen-heading-actions"><span class="screen-balance">✧ ${Number(state.user?.inventory?.GOLD || 0).toLocaleString("ko-KR")} GOLD · ◈ ${Number(state.user?.inventory?.MOON_SHARD || 0).toLocaleString("ko-KR")} 파편 · ◆ ${Number(state.user?.inventory?.GEM || 0).toLocaleString("ko-KR")} GEM</span><button class="button screen-home-button" data-mode="lobby" aria-label="왕국 로비로 돌아가기">⌂ 로비</button></div></div><h3 class="section-label">스킨 컬렉션</h3><div class="skin-grid">${skins.map((skin) => `<article class="skin-card ${skin.owned ? "owned" : "locked"}"><div class="skin-preview ${skinClass(skin.item_id)}"><img src="${skinImage(skin.item_id)}" alt="${escapeAttr(skin.name)} 착용 모습"/><span>${skin.owned ? "✦" : "?"}</span></div><h3>${escapeHtml(skin.name)}</h3><p>${skin.owned ? escapeHtml(skin.style || "보유 스킨") : "꾸미기 상점에서 만날 수 있어요"}</p><div class="skin-card-footer">${skin.owned ? skin.equipped ? `<button class="button" data-unequip>장착 해제</button>` : `<button class="button" data-equip="${escapeAttr(skin.item_id)}">장착하기</button>` : `<b class="muted-label">미보유</b>`}</div></article>`).join("")}</div><h3 class="section-label">최근 재화 기록</h3><div class="ledger-list">${ledger.length ? ledger.map((row) => `<div class="ledger-row"><span class="ledger-delta ${row.delta > 0 ? "positive" : "negative"}">${row.delta > 0 ? "+" : ""}${Number(row.delta).toLocaleString("ko-KR")}</span><b>${escapeHtml(row.item_name)}</b><span>${ledgerSource(row.source_type)}</span><time>${new Date(row.occurred_at).toLocaleString("ko-KR")}</time></div>`).join("") : `<p class="muted-label">아직 재화 변경 기록이 없습니다.</p>`}</div>`;
    root.querySelectorAll("[data-equip]").forEach((button) => button.addEventListener("click", () => equipSkin(button.dataset.equip)));
    root.querySelector("[data-unequip]")?.addEventListener("click", () => equipSkin(null));
    root.querySelector("[data-mode=lobby]").addEventListener("click", () => setMode("lobby"));
    return;
  }
  if (state.mode === "stage") {
    const user = state.user;
    const pushOrder = (user.highest_stage_order || 0) + 1;
    const farmOrder = Math.max(1, user.highest_stage_order || 0);
    const pushStage = state.catalogs.stages.find((item) => item.order === pushOrder);
    const farmStage = state.catalogs.stages.find((item) => item.order === farmOrder);
    $("#dock-title").textContent = "자동 사냥";
    $("#dock-hint").textContent = `최고 클리어 ${user.highest_stage_order} · 현재 모드 ${user.hunt_mode === "push" ? "다음 구역 도전" : "최고 구역 반복"}`;
    if (state.huntActive) {
      root.innerHTML = '<button class="button button-bright return-button" id="toggle-hunt">사냥 일시 정지</button>';
      $("#toggle-hunt").addEventListener("click", () => { state.huntPaused = true; toast("현재 전투가 끝난 뒤 사냥을 멈춥니다."); });
      return;
    }
    const farmName = farmStage?.name || `달빛 초원 ${farmOrder}구역`;
    const pushName = pushStage?.name || `달빛 초원 ${pushOrder}구역`;
    root.innerHTML = `<button class="content-card" data-hunt="farm"><span>⟳</span><b>최고 구역 자동 사냥</b><small>${escapeHtml(farmName)} · 반복 보상 획득</small><i>시작 →</i></button><button class="content-card" data-hunt="push"><span>⚔</span><b>다음 구역 돌파</b><small>${escapeHtml(pushName)} · 승리하면 자동 전진</small><i>도전 →</i></button>`;
    root.querySelectorAll("[data-hunt]").forEach((button) => button.addEventListener("click", () => runStageHunt(button.dataset.hunt)));
    return;
  }
  if (state.mode === "dungeon") {
    const user = state.user;
    const pushFloor = (user.highest_dungeon_floor || 0) + 1;
    const farmFloor = Math.max(1, user.highest_dungeon_floor || 0);
    const label = (floor) => `별무리 폐광 ${floor}층`;
    $("#dock-title").textContent = "별무리 폐광 · 무한 층 원정";
    $("#dock-hint").textContent = `최고 ${user.highest_dungeon_floor || 0}층 · 누적 성공 ${user.dungeon_success_count || 0}회 · 월광 파편은 5회마다 획득`;
    if (state.huntActive) {
      root.innerHTML = '<button class="button button-bright return-button" id="toggle-hunt">도전 일시 정지</button>';
      $("#toggle-hunt").addEventListener("click", () => { state.huntPaused = true; toast("현재 전투가 끝난 뒤 도전을 멈춥니다."); });
      return;
    }
    root.innerHTML = `<button class="content-card" data-dungeon-hunt="farm"><span>⟳</span><b>최고 층 반복 도전</b><small>${label(farmFloor)} · 재화·재료 획득</small><i>시작 →</i></button><button class="content-card" data-dungeon-hunt="push"><span>⬟</span><b>다음 층 돌파</b><small>${label(pushFloor)} · 승리하면 자동 전진</small><i>도전 →</i></button>`;
    root.querySelectorAll("[data-dungeon-hunt]").forEach((button) => button.addEventListener("click", () => runDungeonHunt(button.dataset.dungeonHunt)));
    return;
  }
  if (state.mode === "special" && !live) {
    root.innerHTML = '<div class="choice-empty">승인 이벤트를 적용하거나 로컬 게임 fixture로 특별 던전을 열 수 있습니다.<button class="primary-button" id="open-local-fixture">로컬 특별 던전 열기</button></div>';
    $("#open-local-fixture").addEventListener("click", async () => {
      try {
        await gameApi.activateLocalSpecialDungeon();
        await refresh({ quiet: true });
        toast("로컬 게임 fixture를 열었습니다. 승인 이벤트와는 별도로 표시됩니다.");
      } catch (error) { toast(error.message, true); }
    });
    return;
  }
  const enabled = state.mode !== "special" || Boolean(live?.venue);
  root.innerHTML = options.length ? options.map((content) => {
    const required = content.clear_level ?? content.entry_level ?? 1;
    const unlocked = state.user.level >= (content.unlock_level ?? content.entry_level ?? 1);
    return `<button class="content-card ${unlocked && enabled ? "" : "locked"}" data-content="${escapeAttr(content.id)}" ${unlocked && enabled ? "" : "disabled"}>
      <span>${content.is_boss ? "♛" : state.mode === "special" ? "✧" : "⚔"}</span><b>${escapeHtml(content.name)}</b><small>${state.mode === "special" ? "입장" : "권장"} Lv.${required} · ${content.experience_reward} EXP</small><i>${unlocked ? "공간 입장 →" : "🔒"}</i></button>`;
  }).join("") : `<p class="empty-quest">이 이벤트에 열린 공간이 없습니다.</p>`;
  root.querySelectorAll("[data-content]").forEach((button) => button.addEventListener("click", () => {
    const content = options.find((item) => item.id === button.dataset.content);
    if (content) play(command, content);
  }));
}

async function play(commandType, content) {
  if (state.busy) return;
  state.busy = true;
  $("#action-dock").classList.add("busy");
  const live = activeEvent();
  const runtimeEventId = live ? live.runtime_event_id : null;
  try {
    const lobby = game.scene.getScene("LobbyScene");
    lobby.scene.start("DungeonScene", { mode: state.mode, content, eventId: runtimeEventId, user: state.user });
    await new Promise((resolve) => setTimeout(resolve, 220));
    const result = await gameApi.act(commandType, content.id, runtimeEventId);
    await game.scene.getScene("DungeonScene").resolve(result);
    state.user = result.user;
    await refresh({ quiet: true });
    animateInventoryChanges(result.inventory_changes || []);
    toast(result.attempt.result === "succeeded" ? "서버에서 승리를 확정했습니다." : "도전 결과가 기록되었습니다. 성장 후 다시 도전하세요.", result.attempt.result !== "succeeded");
    showReturnButton();
  } catch (error) {
    returnToWorld();
    toast(error.message, true);
  } finally {
    state.busy = false;
    $("#action-dock").classList.remove("busy");
  }
}

async function runStageHunt(firstMode) {
  if (state.huntActive || state.busy) return;
  state.huntActive = true;
  state.huntPaused = false;
  state.busy = true;
  renderChoices();
  try {
    const lobby = game.scene.getScene("LobbyScene");
    const firstOrder = firstMode === "push" ? state.user.highest_stage_order + 1 : Math.max(1, state.user.highest_stage_order);
    const firstStage = (await gameApi.contentAt("stages", firstOrder)).item;
    lobby.scene.start("DungeonScene", { mode: "stage", content: firstStage, user: state.user });
    await new Promise((resolve) => setTimeout(resolve, 200));
    let mode = firstMode;
    while (!state.huntPaused && state.mode === "stage") {
      const stageOrder = mode === "push"
        ? state.user.highest_stage_order + 1
        : Math.max(1, state.user.highest_stage_order);
      const stage = (await gameApi.contentAt("stages", stageOrder)).item;
      const event = activeEvent();
      const result = await gameApi.act("stage_challenge", stage.id, event?.runtime_event_id || null, mode);
      state.user = result.user;
      const scene = game.scene.getScene("DungeonScene");
      scene.context.user = state.user;
      scene.setEncounter(stage);
      await scene.resolve(result);
      await refresh({ quiet: true });
      animateInventoryChanges(result.inventory_changes || []);
      const progress = result.stage_progress;
      if (progress?.next_action !== "continue_automatically" || state.huntPaused) break;
      mode = progress.hunt_mode;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.huntActive = false;
    state.busy = false;
    returnToWorld();
    renderChoices();
  }
}

async function runDungeonHunt(firstMode) {
  if (state.huntActive || state.busy) return;
  state.huntActive = true;
  state.huntPaused = false;
  state.busy = true;
  renderChoices();
  try {
    const firstOrder = firstMode === "push"
      ? state.user.highest_dungeon_floor + 1
      : Math.max(1, state.user.highest_dungeon_floor);
    const firstFloor = (await gameApi.contentAt("dungeons", firstOrder)).item;
    game.scene.getScene("LobbyScene").scene.start("DungeonScene", {
      mode: "dungeon", content: firstFloor, user: state.user,
    });
    await new Promise((resolve) => setTimeout(resolve, 200));
    let mode = firstMode;
    while (!state.huntPaused && state.mode === "dungeon") {
      const floorOrder = mode === "push"
        ? state.user.highest_dungeon_floor + 1
        : Math.max(1, state.user.highest_dungeon_floor);
      const floor = (await gameApi.contentAt("dungeons", floorOrder)).item;
      const result = await gameApi.act("dungeon_challenge", floor.id, null, null, mode);
      state.user = result.user;
      const scene = game.scene.getScene("DungeonScene");
      scene.context.user = state.user;
      scene.setEncounter(floor);
      await scene.resolve(result);
      await refresh({ quiet: true });
      animateInventoryChanges(result.inventory_changes || []);
      const progress = result.dungeon_progress;
      if (progress?.next_action !== "continue_automatically" || state.huntPaused) break;
      mode = progress.hunt_mode;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.huntActive = false;
    state.busy = false;
    returnToWorld();
    renderChoices();
  }
}

async function grow(growthActionId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const result = await gameApi.grow(growthActionId);
    state.user = result.user;
    render();
    syncWorld();
    const statKey = growthActionId === "weapon_training" ? "attack" : "max_hp";
    toast(`${result.name} 완료 · ${result.stats_before[statKey]} → ${result.stats_after[statKey]}`);
    animateInventoryChanges(result.inventory_changes);
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; }
}

async function purchaseSkin(offerId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const result = await gameApi.purchaseSkin(offerId);
    state.user = result.user;
    await refresh({ quiet: true });
    animateInventoryChanges([result.inventory_change]);
    toast(`${result.skin.name}을(를) 원정대 보관함에 추가했습니다.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = false;
  }
}

async function equipSkin(skinId) {
  if (state.busy) return;
  state.busy = true;
  try {
    const result = await gameApi.equipSkin(skinId);
    state.user = result.user;
    await refresh({ quiet: true });
    toast(skinId ? "스킨을 장착했습니다. 전투 화면에도 적용됩니다." : "스킨을 해제하고 기본 외형으로 돌아왔습니다.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = false;
  }
}

function skinClass(itemId) {
  return ({
    SKIN_STAR_CANDY: "skin-star-candy",
    SKIN_MUSHROOM: "skin-mushroom",
    SKIN_MOON_BUNNY: "skin-moon-bunny",
  })[itemId] || "skin-default";
}

function skinImage(itemId) {
  return ({
    SKIN_STAR_CANDY: starCandySkin,
    SKIN_MUSHROOM: mushroomSkin,
    SKIN_MOON_BUNNY: moonBunnySkin,
  })[itemId] || "/assets/hero-mascot.png";
}

function ledgerSource(sourceType) {
  return ({
    content_clear: "콘텐츠 보상",
    stage_milestone: "첫 클리어 보상",
    dungeon_clear_milestone: "폐광 누적 보상",
    event_mission: "이벤트 미션",
    growth: "영웅 강화",
    shop_purchase: "스킨 구매",
    opening_balance: "시작 보유량",
  })[sourceType] || sourceType;
}

function animateInventoryChanges(changes) {
  const currencyChanges = changes.filter((change) => ["GOLD", "GEM", "MOON_SHARD"].includes(change.item_id));
  const grouped = new Map();
  for (const change of currencyChanges) {
    const rows = grouped.get(change.item_id) || [];
    rows.push(change);
    grouped.set(change.item_id, rows);
  }
  for (const [itemId, rows] of grouped) {
    const target = itemId === "GOLD" ? $("#gold-count") : itemId === "GEM" ? $("#gem-count") : $("#shard-count");
    const destination = target.getBoundingClientRect();
    const start = Number(rows[0].balance_before);
    const end = Number(state.user?.inventory?.[itemId] ?? rows[rows.length - 1].balance_after);
    target.textContent = start.toLocaleString("ko-KR");
    const started = performance.now();
    const countUp = (now) => {
      const ratio = Math.min(1, (now - started) / 650);
      const eased = 1 - (1 - ratio) ** 3;
      target.textContent = Math.round(start + (end - start) * eased).toLocaleString("ko-KR");
      if (ratio < 1) requestAnimationFrame(countUp);
    };
    requestAnimationFrame(countUp);
    for (const change of rows) {
      const amount = change.delta ?? change.quantity;
      const coin = document.createElement("div");
      coin.className = "reward-float";
      if (amount > 0) {
        const source = $("#game-stage").getBoundingClientRect();
        coin.textContent = "+" + amount + " " + (change.item_name || itemName(itemId)) + " · " + (change.source_type === "event_mission" ? "이벤트 보상" : "게임 보상");
        coin.style.left = String(source.left + source.width * 0.55) + "px";
        coin.style.top = String(source.top + source.height * 0.73) + "px";
        document.body.append(coin);
        requestAnimationFrame(() => {
          coin.classList.add("flying");
          coin.style.left = destination.left + "px";
          coin.style.top = destination.top + "px";
        });
      } else {
        coin.textContent = amount + " " + (change.item_name || itemName(itemId)) + " · 강화";
        coin.style.left = destination.left + "px";
        coin.style.top = destination.top + "px";
        document.body.append(coin);
        requestAnimationFrame(() => { coin.classList.add("flying"); coin.style.top = (destination.top - 42) + "px"; });
      }
      setTimeout(() => coin.remove(), 950);
    }
  }
}
function showReturnButton() {
  const root = $("#content-choices");
  root.innerHTML = '<button class="button button-bright return-button" id="return-lobby">원정대 거점으로 돌아가기</button>';
  $("#return-lobby").addEventListener("click", () => {
    returnToWorld();
    renderChoices();
  });
}

function setMode(mode) {
  if (mode === "special" && !activeEvent()?.venue) {
    toast("승인된 기획 이벤트를 적용하면 월광 균열이 열립니다.", true);
    return;
  }
  if (state.huntActive && mode !== "stage") state.huntPaused = true;
  state.mode = mode;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.mode === mode));
  const names = { lobby: "달빛 원정대의 거점", growth: "원정대 성장", stage: "달빛 초원 · 스테이지", dungeon: "별무리 폐광", special: "월광 균열 · 이벤트 던전", collection: "원정대 보관함", shop: "왕국 꾸미기 상점" };
  const titles = { lobby: "다음 모험을 준비하세요", growth: "강화로 더 강해지세요", stage: "새로운 모험이 기다립니다", dungeon: "폐광의 수호자를 마주하세요", special: "봉인의 문이 열렸습니다", collection: "원정대의 수집품", shop: "귀여운 새 외형을 만나보세요" };
  const captions = { lobby: "승인된 이벤트가 적용되면 특별 던전이 열립니다", growth: "재화를 사용해 공격력과 최대 HP를 높이세요", stage: "스테이지를 돌파해 원정대의 레벨을 올리세요", dungeon: "층을 돌파해 월광 파편과 골드를 모으세요", special: "기획안의 임무가 특별 공간의 목표로 변환됩니다", collection: "보유 재화와 스킨, 최근 변경 기록을 확인하세요", shop: "첫 클리어 별빛 보석으로 원정대 외형을 꾸며보세요" };
  $("#location-name").textContent = names[mode];
  $("#scene-title").textContent = titles[mode];
  $("#scene-subtitle").textContent = captions[mode];
  renderChoices();
  syncWorld(mode);
}

function formatDate(value) { return new Date(value).toLocaleDateString("ko-KR", { month: "short", day: "numeric" }); }
function targetLabel(value) { return ({ new_user: "신규 원정대", returning_user: "복귀 원정대", all_user: "모든 원정대" })[value] || value; }
function statusLabel(value) { return ({ active: "진행 중", scheduled: "시작 대기", ended: "종료" })[value] || value; }
function escapeHtml(value) { return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]); }
function escapeAttr(value) { return escapeHtml(value).replace(/`/g, "&#96;"); }

$("#event-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#task-id");
  const taskId = input.value.trim().toUpperCase();
  if (!/^TASK-[A-Z0-9]{12}$/.test(taskId)) { toast("TASK- 뒤에 12자리 ID를 입력해 주세요.", true); return; }
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> 승인 결과 확인 중';
  try {
    const preview = await gameApi.previewEvent(taskId);
    const applied = await gameApi.applyEvent(taskId, preview.result_version);
    await refresh({ quiet: true });
    setMode("special");
    toast(`${preview.event_name} · 승인 버전 v${preview.result_version} 적용 완료`);
    input.value = "";
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.innerHTML = "<span>✦</span> 이벤트 적용";
  }
});

document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
$("#refresh-button").addEventListener("click", () => refresh());
$("#audit-toggle").addEventListener("click", () => {
  const panel = $("#audit-panel");
  panel.classList.toggle("hidden");
  $("#audit-toggle span").textContent = panel.classList.contains("hidden") ? "⌄" : "⌃";
});

refresh({ quiet: true });
