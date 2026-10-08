const $ = (id) => document.getElementById(id);
const PLAYER_KEY = "opong-heroes:village:v1";
const ACTIVE_EVENT_KEY = "opong-heroes:active-event";
const DEFAULT_PLAYER = {
  level: 1, exp: 0, gold: 360, elixir: 260, trophies: 0, potions: 1,
  army: { guard: 3, archer: 2, giant: 0 },
  village: { hall: 1, mine: 1, barracks: 1 },
  lastCollected: 0, inventory: [],
};
const BUILDINGS = {
  hall: { name: "마을회관", icon: "🏰", description: "마을의 중심입니다. 업그레이드하면 원정 보상이 늘어납니다.", x: 3, y: 2, cost: 180 },
  mine: { name: "자원 채굴장", icon: "⛏️", description: "15초마다 금화와 엘릭서를 수확할 수 있습니다.", x: 1, y: 1, cost: 120 },
  barracks: { name: "훈련소", icon: "⚔️", description: "병력을 훈련합니다. Lv.2부터 거인을 사용할 수 있습니다.", x: 5, y: 1, cost: 150 },
};
const TROOPS = {
  guard: { name: "방패병", icon: "🛡️", cost: 24, hp: 34, damage: 10, range: 1, description: "튼튼한 근접 병력" },
  archer: { name: "궁수", icon: "🏹", cost: 28, hp: 20, damage: 9, range: 2, description: "멀리서 지원 사격" },
  giant: { name: "거인", icon: "🗿", cost: 55, hp: 78, damage: 14, range: 1, description: "방어탑을 먼저 공격" },
};
const ENEMY_BASE = [
  { id: "hall", name: "고블린 회관", icon: "🏯", x: 4, y: 3, hp: 70, type: "hall" },
  { id: "tower-left", name: "서쪽 방어탑", icon: "🏹", x: 2, y: 2, hp: 30, type: "tower" },
  { id: "tower-right", name: "동쪽 방어탑", icon: "🏹", x: 6, y: 2, hp: 30, type: "tower" },
  { id: "store", name: "고블린 창고", icon: "🪙", x: 3, y: 5, hp: 24, type: "store" },
  { id: "well", name: "엘릭서 우물", icon: "💧", x: 5, y: 5, hp: 24, type: "store" },
];
const state = {
  player: structuredClone(DEFAULT_PLAYER), event: null, progress: {}, claimedDays: [],
  selectedBuilding: "hall", raid: null, raidTimer: null, toastTimer: null,
};

function safeNumber(value, fallback, max = 999999) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, Math.min(max, Math.floor(number))) : fallback;
}
function save() {
  localStorage.setItem(PLAYER_KEY, JSON.stringify(state.player));
  if (state.event) localStorage.setItem(eventKey(), JSON.stringify({ progress: state.progress, claimedDays: state.claimedDays }));
}
function restorePlayer() {
  try {
    const saved = JSON.parse(localStorage.getItem(PLAYER_KEY) || "null");
    if (!saved || typeof saved !== "object") return;
    for (const key of ["level", "exp", "gold", "elixir", "trophies", "potions", "lastCollected"]) {
      state.player[key] = safeNumber(saved[key], DEFAULT_PLAYER[key]);
    }
    state.player.level = Math.max(1, Math.min(50, state.player.level));
    for (const key of Object.keys(DEFAULT_PLAYER.army)) state.player.army[key] = safeNumber(saved.army?.[key], DEFAULT_PLAYER.army[key], 20);
    for (const key of Object.keys(DEFAULT_PLAYER.village)) state.player.village[key] = Math.max(1, Math.min(3, safeNumber(saved.village?.[key], 1, 3)));
    state.player.inventory = Array.isArray(saved.inventory) ? saved.inventory.filter((item) => item && typeof item.name === "string" && Number.isFinite(item.quantity)) : [];
  } catch (_) { localStorage.removeItem(PLAYER_KEY); }
}
function eventKey() { return `opong-heroes:event:${state.event.task_id}:${state.event.result_version}`; }
function restoreEventProgress() {
  state.progress = {};
  state.claimedDays = [];
  try {
    const saved = JSON.parse(localStorage.getItem(eventKey()) || "null");
    if (saved && typeof saved.progress === "object" && saved.progress !== null) {
      state.progress = saved.progress;
      state.claimedDays = Array.isArray(saved.claimedDays) ? saved.claimedDays : [];
    }
  } catch (_) { localStorage.removeItem(eventKey()); }
}
function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.add("hidden"), 3600);
}
function showScreen(name) {
  if (state.raid && name !== "raid") { showToast("원정을 마치려면 먼저 ‘마을로’를 눌러 철수하세요."); return; }
  for (const screen of document.querySelectorAll(".screen")) screen.classList.toggle("hidden", screen.id !== `${name}-screen`);
  if (name === "village") renderVillage();
  if (name === "event") renderEvent();
  if (name === "inventory") renderInventory();
  if (name === "raid") renderRaid();
  window.scrollTo(0, 0);
}
function armyCount() { return Object.values(state.player.army).reduce((sum, count) => sum + count, 0); }
function armyCapacity() { return 4 + state.player.village.barracks * 2; }
function expGoal() { return state.player.level * 20; }
function renderHud() {
  $("hero-level").textContent = `Lv.${state.player.level} 오퐁 대장`;
  $("exp-text").textContent = `${state.player.exp} / ${expGoal()} XP`;
  $("exp-bar").style.width = `${Math.min(100, state.player.exp / expGoal() * 100)}%`;
  $("gold-count").textContent = state.player.gold.toLocaleString();
  $("elixir-count").textContent = state.player.elixir.toLocaleString();
  $("trophy-count").textContent = state.player.trophies;
  $("event-dot").classList.toggle("hidden", !state.event);
}
function collectReady() { return Date.now() - state.player.lastCollected >= 15000; }
function upgradeCost(key) { return BUILDINGS[key].cost * state.player.village[key]; }
function renderVillage() {
  const board = $("village-board");
  board.replaceChildren();
  for (let y = 0; y < 5; y += 1) for (let x = 0; x < 7; x += 1) {
    const tile = document.createElement("div");
    tile.className = "village-tile";
    const entry = Object.entries(BUILDINGS).find(([, building]) => building.x === x && building.y === y);
    if (entry) {
      const [key, building] = entry;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `building ${state.selectedBuilding === key ? "selected" : ""}`;
      button.innerHTML = `<span aria-hidden="true">${building.icon}</span><strong>${building.name}</strong><small>Lv.${state.player.village[key]}</small>`;
      button.setAttribute("aria-label", `${building.name} 레벨 ${state.player.village[key]} 선택`);
      button.addEventListener("click", () => { state.selectedBuilding = key; renderVillage(); });
      tile.append(button);
    } else if ((x + y * 3) % 8 === 0) {
      const tree = document.createElement("span"); tree.className = "map-decor"; tree.textContent = "🌲"; tile.append(tree);
    }
    board.append(tile);
  }
  const key = state.selectedBuilding;
  const building = BUILDINGS[key];
  const level = state.player.village[key];
  $("building-name").textContent = `${building.icon} ${building.name}`;
  $("building-description").textContent = building.description;
  $("building-level").textContent = `현재 Lv.${level} · 최대 Lv.3`;
  $("upgrade-button").textContent = level >= 3 ? "최대 레벨" : `🪙 ${upgradeCost(key)} · Lv.${level + 1} 업그레이드`;
  $("upgrade-button").disabled = level >= 3 || state.player.gold < upgradeCost(key);
  $("collect-button").classList.toggle("hidden", key !== "mine");
  renderCollectButton();
  renderTraining();
  renderHud();
}
function renderCollectButton() {
  const seconds = Math.max(0, Math.ceil((15000 - (Date.now() - state.player.lastCollected)) / 1000));
  const level = state.player.village.mine;
  $("collect-button").textContent = seconds ? `자원 준비 중 · ${seconds}초` : `🪙 ${35 * level} + 💧 ${25 * level} 수확`;
  $("collect-button").disabled = seconds > 0;
}
function renderTraining() {
  $("army-count").textContent = armyCount();
  $("army-capacity").textContent = armyCapacity();
  const list = $("training-list");
  list.replaceChildren();
  for (const [key, troop] of Object.entries(TROOPS)) {
    const locked = key === "giant" && state.player.village.barracks < 2;
    const row = document.createElement("div"); row.className = "training-row";
    const info = document.createElement("div");
    info.innerHTML = `<strong>${troop.icon} ${troop.name} <em>×${state.player.army[key]}</em></strong><small>${locked ? "훈련소 Lv.2 필요" : troop.description}</small>`;
    const button = document.createElement("button"); button.type = "button";
    button.textContent = locked ? "잠김" : `+ 💧${troop.cost}`;
    button.disabled = locked || armyCount() >= armyCapacity() || state.player.elixir < troop.cost;
    button.addEventListener("click", () => trainTroop(key));
    row.append(info, button); list.append(row);
  }
}
function upgradeBuilding() {
  const key = state.selectedBuilding;
  const level = state.player.village[key];
  const cost = upgradeCost(key);
  if (level >= 3 || state.player.gold < cost) return;
  state.player.gold -= cost;
  state.player.village[key] += 1;
  save(); renderVillage();
  showToast(`${BUILDINGS[key].name}이 Lv.${level + 1}로 성장했습니다!`);
}
function collectResources() {
  if (!collectReady()) return;
  const level = state.player.village.mine;
  state.player.gold += 35 * level;
  state.player.elixir += 25 * level;
  state.player.lastCollected = Date.now();
  save(); renderVillage(); showToast("채굴장에서 자원을 수확했습니다!");
}
function trainTroop(key) {
  const troop = TROOPS[key];
  if ((key === "giant" && state.player.village.barracks < 2) || armyCount() >= armyCapacity() || state.player.elixir < troop.cost) return;
  state.player.elixir -= troop.cost;
  state.player.army[key] += 1;
  save(); renderVillage(); showToast(`${troop.name} 훈련 완료!`);
}

function missionGoal(mission) { return Math.max(1, safeNumber(mission.target_count, 1)); }
function missionCount(index, mission) {
  if (mission.mission_type === "level_reach") return Math.min(state.player.level, missionGoal(mission));
  return Math.min(safeNumber(state.progress[index], 0), missionGoal(mission));
}
function dayComplete(day) {
  const entries = state.event.missions.map((mission, index) => ({ mission, index })).filter(({ mission }) => mission.day === day);
  return entries.length > 0 && entries.every(({ mission, index }) => missionCount(index, mission) >= missionGoal(mission));
}
function activeDay() {
  if (!state.event) return 1;
  for (let day = 1; day <= state.event.duration; day += 1) if (!dayComplete(day)) return day;
  return state.event.duration + 1;
}
function grantReward(reward) {
  const name = reward.item_name || reward.item_id || "이벤트 보상";
  const quantity = Math.max(1, safeNumber(reward.quantity, 1, 999));
  if (/potion|포션/i.test(`${reward.item_id} ${name}`)) state.player.potions += quantity;
  else if (/gold|골드/i.test(`${reward.item_id} ${name}`)) state.player.gold += quantity;
  else if (/elixir|엘릭서/i.test(`${reward.item_id} ${name}`)) state.player.elixir += quantity;
  else state.player.inventory.push({ name, quantity });
  return `${name} ×${quantity}`;
}
function checkDayRewards() {
  if (!state.event) return;
  for (let day = 1; day <= state.event.duration; day += 1) {
    if (state.claimedDays.includes(day) || !dayComplete(day)) continue;
    state.claimedDays.push(day);
    const rewards = state.event.rewards.filter((reward) => reward.day === day).map(grantReward);
    showToast(`${day}일차 미션 완료! ${rewards.length ? `보상: ${rewards.join(", ")}` : ""}`);
  }
  save(); renderHud();
}
function advanceMissions(type, amount = 1, day = activeDay()) {
  if (!state.event) return;
  for (const [index, mission] of state.event.missions.entries()) {
    if (mission.day !== day) continue;
    if ((type === "destroy" && mission.mission_type === "monster_kill" && (!mission.content_id || mission.content_id === "HUNT_001")) ||
        (type === "destroy" && mission.mission_type === "building_destroy" && mission.content_id === "COC_BUILDING_DESTROY") ||
        (type === "raid" && mission.mission_type === "dungeon_clear") ||
        (type === "raid" && mission.mission_type === "raid_win" && mission.content_id === "COC_RAID_WIN") ||
        (type === "trophy" && mission.mission_type === "trophy_gain" && mission.content_id === "COC_TROPHY_GAIN")) {
      state.progress[index] = missionCount(index, mission) + amount;
    }
  }
  checkDayRewards();
}
function gainExp(amount) {
  state.player.exp += amount;
  while (state.player.exp >= expGoal()) {
    state.player.exp -= expGoal();
    state.player.level += 1;
    showToast(`오퐁 대장이 Lv.${state.player.level}로 성장했습니다!`);
  }
  checkDayRewards();
}
function renderEvent() {
  const list = $("mission-list"); list.replaceChildren();
  if (!state.event) {
    $("event-name").textContent = "이벤트를 연결해 주세요";
    $("event-meta").textContent = "운영자 화면에서 승인된 Task ID를 입력하세요.";
    $("event-day-count").textContent = "대기 중";
    return;
  }
  $("event-name").textContent = state.event.event_name;
  const sample = state.event.sample_summary;
  $("event-meta").textContent = state.event.game_source === "clash_of_clans" && sample
    ? `Clash 공개 상위 순위 표본 · 플레이어 ${sample.player_count}명 / 클랜 ${sample.clan_count}개 · ${sample.sampled_at}`
    : `${state.event.target_user} · 게임 데이터 ${state.event.game_data_version || "미확인"}`;
  $("event-day-count").textContent = `${state.event.duration}일 이벤트`;
  for (const [index, mission] of state.event.missions.entries()) {
    const row = document.createElement("div"); row.className = "mission-row";
    const count = missionCount(index, mission); const goal = missionGoal(mission);
    if (count >= goal) row.classList.add("complete");
    if (mission.day > activeDay()) row.classList.add("future");
    const adaptation = { monster_kill: "적 건물 파괴", dungeon_clear: "적 기지 점령", level_reach: "대장 레벨 달성", building_destroy: "적 건물 파괴", raid_win: "적 기지 점령", trophy_gain: "데모 트로피 획득" }[mission.mission_type] || "진행 중";
    const info = document.createElement("div");
    const title = document.createElement("strong"); title.textContent = `Day ${mission.day} · ${mission.mission_name || mission.mission_type}`;
    const subtitle = document.createElement("small"); subtitle.textContent = `데모 진행 방식: ${adaptation}`;
    const track = document.createElement("div"); track.className = "mission-track";
    const fill = document.createElement("span"); fill.style.width = `${Math.min(100, count / goal * 100)}%`; track.append(fill);
    info.append(title, subtitle, track);
    const status = document.createElement("b"); status.textContent = `${count} / ${goal}`;
    row.append(info, status); list.append(row);
  }
}
function renderInventory() {
  const list = $("inventory-list"); list.replaceChildren();
  const items = [`🪙 금화 ×${state.player.gold}`, `💧 엘릭서 ×${state.player.elixir}`, `🧪 회복 포션 ×${state.player.potions}`, ...state.player.inventory.map((item) => `🎁 ${item.name} ×${item.quantity}`)];
  for (const item of items) { const card = document.createElement("div"); card.className = "inventory-item"; card.textContent = item; list.append(card); }
}

function isEdge(x, y) { return x === 0 || x === 8 || y === 0 || y === 6; }
function distance(a, b) { return Math.abs(a.x - b.x) + Math.abs(a.y - b.y); }
function startRaid() {
  if (armyCount() === 0) { showToast("훈련소에서 병력을 먼저 준비하세요."); return; }
  state.raid = {
    buildings: ENEMY_BASE.map((building) => ({ ...building, maxHp: building.hp })),
    units: [], selected: Object.keys(TROOPS).find((key) => state.player.army[key] > 0),
    started: false, elapsed: 0, ticks: 0, deployed: 0, destroyed: 0,
    eventDay: activeDay(),
  };
  $("result-backdrop").classList.add("hidden");
  clearInterval(state.raidTimer);
  state.raidTimer = setInterval(tickRaid, 650);
  showScreen("raid");
}
function deployTroop(x, y) {
  const raid = state.raid;
  if (!raid || !isEdge(x, y) || !raid.selected || state.player.army[raid.selected] <= 0) return;
  const troop = TROOPS[raid.selected];
  raid.units.push({ type: raid.selected, x, y, hp: troop.hp, maxHp: troop.hp });
  state.player.army[raid.selected] -= 1;
  raid.started = true; raid.deployed += 1;
  if (state.player.army[raid.selected] === 0) raid.selected = Object.keys(TROOPS).find((key) => state.player.army[key] > 0) || null;
  save(); renderRaid(); renderHud();
}
function chooseTarget(unit, buildings) {
  const alive = buildings.filter((building) => building.hp > 0);
  const targets = unit.type === "giant" && alive.some((building) => building.type === "tower") ? alive.filter((building) => building.type === "tower") : alive;
  return targets.sort((a, b) => distance(unit, a) - distance(unit, b))[0];
}
function tickRaid() {
  const raid = state.raid;
  if (!raid || !raid.started) return;
  raid.ticks += 1; raid.elapsed += 0.65;
  for (const unit of raid.units) {
    if (unit.hp <= 0) continue;
    const target = chooseTarget(unit, raid.buildings);
    if (!target) break;
    const troop = TROOPS[unit.type];
    if (distance(unit, target) <= troop.range) {
      target.hp = Math.max(0, target.hp - troop.damage);
      if (target.hp === 0 && !target.destroyed) {
        target.destroyed = true; raid.destroyed += 1; advanceMissions("destroy", 1, raid.eventDay);
      }
    } else if (unit.x !== target.x) unit.x += Math.sign(target.x - unit.x);
    else unit.y += Math.sign(target.y - unit.y);
    if (raid.buildings[0].hp === 0) { finishRaid(true); return; }
  }
  if (raid.ticks % 2 === 0) for (const tower of raid.buildings.filter((building) => building.type === "tower" && building.hp > 0)) {
    const target = raid.units.filter((unit) => unit.hp > 0 && distance(unit, tower) <= 3).sort((a, b) => distance(a, tower) - distance(b, tower))[0];
    if (target) target.hp = Math.max(0, target.hp - 5);
  }
  raid.units = raid.units.filter((unit) => unit.hp > 0);
  if (raid.elapsed >= 60 || (raid.deployed > 0 && raid.units.length === 0 && armyCount() === 0)) { finishRaid(false); return; }
  renderRaid();
}
function finishRaid(won, retreated = false) {
  const raid = state.raid;
  if (!raid) return;
  clearInterval(state.raidTimer); state.raidTimer = null;
  const gold = retreated ? 0 : (won ? 90 + raid.destroyed * 12 + state.player.village.hall * 15 : raid.destroyed * 10);
  const elixir = retreated ? 0 : (won ? 70 + raid.destroyed * 8 : raid.destroyed * 7);
  state.player.gold += gold; state.player.elixir += elixir;
  if (won) { state.player.trophies += 3; gainExp(15); advanceMissions("raid", 1, raid.eventDay); advanceMissions("trophy", 3, raid.eventDay); }
  save(); renderHud();
  $("result-stars").textContent = won ? (raid.destroyed >= 4 ? "★ ★ ★" : "★ ★ ☆") : "☆ ☆ ☆";
  $("result-title").textContent = won ? "기지 점령 성공!" : retreated ? "원정 철수" : "기지 점령 실패";
  $("result-message").textContent = won ? "병력 배치가 승리를 만들었습니다. 전리품으로 마을을 키워 보세요." : "병력을 다시 훈련하고 다른 위치에서 도전해 보세요.";
  $("result-loot").textContent = `파괴 ${raid.destroyed}/${raid.buildings.length} · 🪙 +${gold} · 💧 +${elixir}${won ? " · 🏆 +3" : ""}`;
  $("result-backdrop").classList.remove("hidden");
  state.raid = null;
}
function renderRaid() {
  const raid = state.raid;
  if (!raid) return;
  $("raid-time").textContent = Math.max(0, Math.ceil(60 - raid.elapsed));
  $("raid-destruction").textContent = `${Math.round(raid.destroyed / raid.buildings.length * 100)}% 파괴`;
  $("raid-status").textContent = raid.started ? `${raid.units.length}명 전투 중 · 남은 병력 ${armyCount()}명` : "병력을 선택하고 밝은 테두리 칸을 클릭하세요.";
  const board = $("raid-board"); board.replaceChildren();
  for (let y = 0; y < 7; y += 1) for (let x = 0; x < 9; x += 1) {
    const tile = document.createElement("button"); tile.type = "button";
    tile.className = `raid-tile ${isEdge(x, y) ? "deploy-zone" : ""}`;
    const building = raid.buildings.find((item) => item.x === x && item.y === y && item.hp > 0);
    const units = raid.units.filter((unit) => unit.x === x && unit.y === y);
    if (building) {
      const icon = document.createElement("span"); icon.className = "enemy-building"; icon.textContent = building.icon;
      const hp = document.createElement("span"); hp.className = "tile-hp"; hp.style.width = `${building.hp / building.maxHp * 100}%`;
      tile.append(icon, hp);
    }
    if (units.length) {
      const troop = document.createElement("span"); troop.className = "friendly-unit";
      troop.textContent = `${TROOPS[units[0].type].icon}${units.length > 1 ? `×${units.length}` : ""}`;
      tile.append(troop);
    }
    tile.setAttribute("aria-label", building ? `${building.name}, 체력 ${building.hp}` : units.length ? `아군 병력 ${units.length}명` : isEdge(x, y) ? "병력 배치 칸" : "빈 칸");
    if (isEdge(x, y)) tile.addEventListener("click", () => deployTroop(x, y));
    board.append(tile);
  }
  const list = $("deploy-list"); list.replaceChildren();
  for (const [key, troop] of Object.entries(TROOPS)) {
    if (key === "giant" && state.player.village.barracks < 2 && state.player.army[key] === 0) continue;
    const button = document.createElement("button"); button.type = "button";
    button.className = `deploy-card ${raid.selected === key ? "selected" : ""}`;
    button.textContent = `${troop.icon} ${troop.name} ×${state.player.army[key]}`;
    button.disabled = state.player.army[key] === 0;
    button.addEventListener("click", () => { raid.selected = key; renderRaid(); });
    list.append(button);
  }
  $("potion-count").textContent = state.player.potions;
  $("potion-button").disabled = state.player.potions === 0 || raid.units.length === 0;
}
function usePotion() {
  if (!state.raid || !state.raid.units.length || state.player.potions === 0) return;
  state.player.potions -= 1;
  for (const unit of state.raid.units) unit.hp = Math.min(unit.maxHp, unit.hp + 18);
  save(); renderRaid(); showToast("출정 병력이 회복됐습니다!");
}
function setMessage(message, error = false) { $("message").textContent = message; $("message").classList.toggle("error", error); }
async function loadEvent(taskId) {
  if (!/^TASK-[A-Z0-9]{12}$/.test(taskId)) { setMessage("Task ID 형식을 확인해 주세요.", true); return; }
  $("load-button").disabled = true; setMessage("승인된 이벤트를 불러오는 중입니다…");
  try {
    const response = await fetch(`/game-api/task/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "이벤트를 불러오지 못했습니다.");
    if (!Array.isArray(payload.missions) || !payload.missions.length) throw new Error("플레이 가능한 미션이 없습니다.");
    state.event = payload; restoreEventProgress(); checkDayRewards();
    localStorage.setItem(ACTIVE_EVENT_KEY, taskId);
    $("task-id").value = taskId;
    renderEvent(); renderHud(); save();
    setMessage("이벤트가 마을에 적용됐습니다. 원정에서 미션을 진행해 보세요!");
  } catch (error) { setMessage(error.message || "연결 오류가 발생했습니다.", true); }
  finally { $("load-button").disabled = false; }
}

restorePlayer();
renderVillage();
document.querySelectorAll("[data-screen]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.screen)));
$("upgrade-button").addEventListener("click", upgradeBuilding);
$("collect-button").addEventListener("click", collectResources);
$("raid-button").addEventListener("click", startRaid);
$("retreat-button").addEventListener("click", () => finishRaid(false, true));
$("potion-button").addEventListener("click", usePotion);
$("result-close").addEventListener("click", () => { $("result-backdrop").classList.add("hidden"); showScreen("village"); });
$("load-form").addEventListener("submit", (event) => { event.preventDefault(); loadEvent($("task-id").value.trim().toUpperCase()); });
setInterval(() => { if (!$("village-screen").classList.contains("hidden")) renderCollectButton(); }, 1000);
const activeTask = localStorage.getItem(ACTIVE_EVENT_KEY);
if (activeTask) loadEvent(activeTask);
