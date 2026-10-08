const $ = (id) => document.getElementById(id);
const PLAYER_KEY = "opong-heroes:player:v2";
const state = {
  player: { level: 1, exp: 0, hp: 30, gold: 0, potions: 3, inventory: [] },
  event: null,
  progress: {},
  claimedDays: [],
  battle: null,
  afterModal: null,
  modalQueue: [],
};

function eventKey() {
  return `opong-heroes:event:${state.event.task_id}:${state.event.result_version}`;
}

function maxHp() { return 30 + (state.player.level - 1) * 6; }
function attackPower() { return 6 + (state.player.level - 1) * 2; }
function expGoal() { return state.player.level * 20; }

function save() {
  localStorage.setItem(PLAYER_KEY, JSON.stringify(state.player));
  if (state.event) localStorage.setItem(eventKey(), JSON.stringify({ progress: state.progress, claimedDays: state.claimedDays }));
}

function restorePlayer() {
  try {
    const saved = JSON.parse(localStorage.getItem(PLAYER_KEY) || "null");
    if (saved && Number.isInteger(saved.level) && saved.level >= 1 && saved.level <= 100) {
      state.player = { ...state.player, ...saved };
      state.player.hp = Math.max(0, Math.min(maxHp(), Number(state.player.hp) || 0));
      state.player.inventory = Array.isArray(saved.inventory) ? saved.inventory : [];
    }
  } catch (_) { localStorage.removeItem(PLAYER_KEY); }
}

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

function showScreen(name) {
  for (const screen of document.querySelectorAll(".screen")) screen.classList.toggle("hidden", screen.id !== `${name}-screen`);
  $("scene").classList.toggle("dungeon-scene", name === "battle" && state.battle?.mode === "dungeon");
  if (name === "event") renderEvent();
  if (name === "contents") renderContents();
  if (name === "inventory") renderInventory();
  if (name === "lobby") renderLobby();
}

function renderHud() {
  $("hero-level").textContent = `Lv.${state.player.level} 오퐁 용사`;
  $("exp-text").textContent = `${state.player.exp} / ${expGoal()}`;
  $("exp-bar").style.width = `${Math.min(100, state.player.exp / expGoal() * 100)}%`;
  $("gold-count").textContent = state.player.gold;
  $("potion-count").textContent = state.player.potions;
  $("event-dot").classList.toggle("hidden", !state.event);
}

function renderLobby() {
  $("lobby-tip").textContent = state.event
    ? `${state.event.event_name} 진행 중 · 이벤트 메뉴에서 미션을 확인하세요!`
    : "일반 사냥에서 슬라임을 쓰러뜨려 보세요!";
}

function renderContents() {
  const unlocked = state.player.level >= 3;
  $("dungeon-lock-text").textContent = unlocked ? "던전 고블린에게 도전!" : "Lv.3 달성 시 해금";
  $("dungeon-level").textContent = unlocked ? "입장 가능" : "Lv.3 필요";
  document.querySelector("[data-mode=dungeon]").classList.toggle("locked", !unlocked);
}

function setMessage(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}

function missionGoal(mission) { return Math.max(1, Number(mission.target_count) || 1); }
function missionCount(index, mission) {
  if (mission.mission_type === "level_reach") return Math.min(state.player.level, missionGoal(mission));
  return Math.min(Number(state.progress[index]) || 0, missionGoal(mission));
}

function dayComplete(day) {
  const entries = state.event.missions.map((mission, index) => ({ mission, index })).filter((entry) => entry.mission.day === day);
  return entries.length > 0 && entries.every(({ mission, index }) => missionCount(index, mission) >= missionGoal(mission));
}

function activeDay() {
  if (!state.event) return 1;
  for (let day = 1; day <= state.event.duration; day += 1) {
    if (!dayComplete(day)) return day;
  }
  return state.event.duration + 1;
}

function grantReward(reward) {
  const name = reward.item_name || reward.item_id || "이벤트 보상";
  const quantity = Math.max(1, Number(reward.quantity) || 1);
  if (/potion|포션/i.test(`${reward.item_id} ${name}`)) state.player.potions += quantity;
  else if (/gold|골드/i.test(`${reward.item_id} ${name}`)) state.player.gold += quantity;
  else state.player.inventory.push({ name, quantity });
  return `${name} ×${quantity}`;
}

function checkDayRewards() {
  if (!state.event) return;
  for (let day = 1; day <= state.event.duration; day += 1) {
    if (state.claimedDays.includes(day) || !dayComplete(day)) continue;
    state.claimedDays.push(day);
    const names = state.event.rewards.filter((reward) => reward.day === day).map(grantReward);
    if (names.length) showModal("DAY CLEAR!", `${day}일차 미션 완료`, [`획득 보상: ${names.join(", ")}`]);
  }
  save();
  renderHud();
  renderEvent();
}

function advanceMissions(type, contentId) {
  if (!state.event) return;
  const day = activeDay();
  for (const [index, mission] of state.event.missions.entries()) {
    if (mission.day !== day) continue;
    const isHunt = type === "kill" && mission.mission_type === "monster_kill" && (!mission.content_id || mission.content_id === contentId);
    const isDungeon = type === "dungeon" && mission.mission_type === "dungeon_clear";
    if (isHunt || isDungeon) state.progress[index] = missionCount(index, mission) + 1;
  }
  checkDayRewards();
}

function renderEvent() {
  const list = $("mission-list");
  list.replaceChildren();
  if (!state.event) {
    $("event-name").textContent = "이벤트를 연결해 주세요";
    $("event-meta").textContent = "운영자 화면에서 승인한 Task ID를 입력하세요.";
    $("event-day-count").textContent = "대기 중";
    return;
  }
  $("event-name").textContent = state.event.event_name;
  $("event-meta").textContent = `${state.event.target_user} · 게임 데이터 ${state.event.game_data_version || "미확인"} · 보상은 브라우저에만 적용`;
  $("event-day-count").textContent = `${state.event.duration}일 이벤트`;
  for (const [index, mission] of state.event.missions.entries()) {
    const row = document.createElement("div");
    row.className = "mission-row";
    const count = missionCount(index, mission);
    const goal = missionGoal(mission);
    if (count >= goal) row.classList.add("complete");
    if (mission.day > activeDay()) row.classList.add("future");
    const day = document.createElement("span");
    day.className = "mission-day";
    day.textContent = `Day ${mission.day}`;
    const main = document.createElement("div");
    main.className = "mission-main";
    const title = document.createElement("strong");
    title.textContent = mission.mission_name || mission.mission_type;
    const progress = document.createElement("div");
    progress.className = "mission-progress";
    const fill = document.createElement("span");
    fill.style.width = `${Math.min(100, count / goal * 100)}%`;
    progress.append(fill);
    main.append(title, progress);
    const status = document.createElement("span");
    status.className = "mission-status";
    status.textContent = `${count} / ${goal}${count >= goal ? " ✓" : ""}`;
    row.append(day, main, status);
    list.append(row);
  }
}

function renderInventory() {
  const list = $("inventory-list");
  list.replaceChildren();
  const items = [
    `🧪 회복 포션 ×${state.player.potions}`,
    `🪙 골드 ×${state.player.gold}`,
    ...state.player.inventory.map((item) => `🎁 ${item.name} ×${item.quantity}`),
  ];
  for (const item of items) {
    const card = document.createElement("div");
    card.className = "inventory-item";
    card.textContent = item;
    list.append(card);
  }
}

function showModal(title, message, details = [], after = null) {
  if (!$('modal-backdrop').classList.contains('hidden')) {
    state.modalQueue.push({ title, message, details, after });
    return;
  }
  state.afterModal = after;
  $("modal-title").textContent = title;
  $("modal-message").textContent = message;
  const box = $("modal-details");
  box.replaceChildren();
  for (const detail of details) {
    const row = document.createElement("div");
    row.textContent = detail;
    box.append(row);
  }
  $("modal-backdrop").classList.remove("hidden");
  $("modal-close").focus();
}

function gainExp(amount) {
  state.player.exp += amount;
  while (state.player.exp >= expGoal()) {
    const goal = expGoal();
    state.player.exp -= goal;
    state.player.level += 1;
    state.player.hp = maxHp();
    state.player.gold += 50;
    state.player.potions += 1;
    showModal("LEVEL UP!", `Lv.${state.player.level} 달성!`, [
      `❤️ 최대 HP ${maxHp() - 6} → ${maxHp()}`,
      `⚔️ 공격력 ${attackPower() - 2} → ${attackPower()}`,
      "🪙 골드 +50 · 🧪 회복 포션 +1",
      ...(state.player.level === 3 ? ["🏰 던전 콘텐츠 해금!"] : []),
    ]);
  }
  checkDayRewards();
  renderHud();
}

function startBattle(mode) {
  if (mode === "dungeon" && state.player.level < 3) {
    showModal("아직 잠겨 있어요", "Lv.3부터 던전에 입장할 수 있습니다.", ["일반 사냥에서 경험치를 모아보세요."]);
    return;
  }
  state.battle = { mode, round: 1, hp: 0, max: 0, won: false };
  state.player.hp = Math.max(1, state.player.hp);
  spawnEnemy();
  showScreen("battle");
  save();
}

function spawnEnemy() {
  const battle = state.battle;
  battle.max = battle.mode === "dungeon" ? 28 + state.player.level * 2 : 18 + state.player.level * 2;
  battle.hp = battle.max;
  battle.won = false;
  $("battle-title").textContent = battle.mode === "dungeon" ? "던전 고블린" : "일반 사냥";
  $("battle-subtitle").textContent = battle.mode === "dungeon" ? "던전 전투" : `초원 슬라임 ${battle.round}번째`;
  $("enemy-name").textContent = battle.mode === "dungeon" ? "던전 고블린" : "초원 슬라임";
  $("battle-enemy").src = battle.mode === "dungeon" ? "/assets/goblin.png" : "/assets/slime.png";
  $("battle-enemy").alt = battle.mode === "dungeon" ? "던전 고블린" : "초원 슬라임";
  $("attack-button").textContent = "⚔️ 공격하기";
  $("battle-log").textContent = battle.mode === "dungeon" ? "🏰 던전 고블린이 길을 막아섰습니다!" : "👾 초원 슬라임이 나타났습니다!";
  renderBattle();
}

function renderBattle() {
  if (!state.battle) return;
  $("battle-hero-level").textContent = state.player.level;
  $("hero-hp-text").textContent = `${state.player.hp} / ${maxHp()}`;
  $("hero-hp-bar").style.width = `${Math.max(0, state.player.hp / maxHp() * 100)}%`;
  $("enemy-hp-text").textContent = `${Math.max(0, state.battle.hp)} / ${state.battle.max}`;
  $("enemy-hp-bar").style.width = `${Math.max(0, state.battle.hp / state.battle.max * 100)}%`;
  $("potion-button").disabled = state.player.potions < 1 || state.player.hp >= maxHp() || state.battle.won;
}

function flashDamage(amount) {
  $("hit-number").textContent = `-${amount}`;
  $("hit-number").classList.remove("hidden", "animate");
  void $("hit-number").offsetWidth;
  $("hit-number").classList.add("animate");
}

function attack() {
  const battle = state.battle;
  if (!battle) return;
  if (battle.won) {
    if (battle.mode === "dungeon") { showScreen("contents"); return; }
    battle.round += 1;
    spawnEnemy();
    return;
  }
  const damage = attackPower();
  battle.hp = Math.max(0, battle.hp - damage);
  flashDamage(damage);
  if (battle.hp === 0) {
    battle.won = true;
    const dungeon = battle.mode === "dungeon";
    state.player.gold += dungeon ? 80 : 8;
    if (dungeon) advanceMissions("dungeon", "DUNGEON_001");
    else advanceMissions("kill", "HUNT_001");
    gainExp(dungeon ? 15 : 10);
    $("battle-log").textContent = dungeon
      ? "✨ 던전 고블린을 물리쳤습니다! 경험치 +15 · 골드 +80"
      : "✨ 초원 슬라임 처치! 경험치 +10 · 골드 +8";
    $("attack-button").textContent = dungeon ? "🏰 콘텐츠로 돌아가기" : "⚔️ 다음 몬스터";
  } else {
    const retaliation = battle.mode === "dungeon" ? 6 : 4;
    state.player.hp = Math.max(0, state.player.hp - retaliation);
    $("battle-log").textContent = `⚔️ ${damage} 피해를 주었습니다. 몬스터의 반격! HP -${retaliation}`;
    if (state.player.hp === 0) {
      showModal("모험 실패", "용사가 쓰러졌습니다.", ["HP를 회복하고 다시 도전하세요. 경험치와 보상은 잃지 않습니다."], () => {
        state.player.hp = maxHp();
        state.battle = null;
        renderHud();
        showScreen("lobby");
        save();
      });
    }
  }
  renderHud();
  renderBattle();
  save();
}

function usePotion() {
  if (!state.battle || state.battle.won || state.player.potions < 1 || state.player.hp >= maxHp()) return;
  state.player.potions -= 1;
  const healed = Math.min(15, maxHp() - state.player.hp);
  state.player.hp += healed;
  $("battle-log").textContent = `🧪 회복 포션을 사용해 HP ${healed} 회복!`;
  renderBattle();
  renderHud();
  save();
}

restorePlayer();
renderHud();
renderLobby();
document.querySelectorAll("[data-screen]").forEach((button) => button.addEventListener("click", () => showScreen(button.dataset.screen)));
document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => startBattle(button.dataset.mode)));
$("brand-button").addEventListener("click", () => showScreen("lobby"));
$("attack-button").addEventListener("click", attack);
$("potion-button").addEventListener("click", usePotion);
$("retreat-button").addEventListener("click", () => { state.battle = null; showScreen("lobby"); save(); });
$("modal-close").addEventListener("click", () => {
  $("modal-backdrop").classList.add("hidden");
  const after = state.afterModal;
  state.afterModal = null;
  if (after) after();
  const next = state.modalQueue.shift();
  if (next) showModal(next.title, next.message, next.details, next.after);
});
$("load-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const taskId = $("task-id").value.trim().toUpperCase();
  if (!/^TASK-[A-Z0-9]{12}$/.test(taskId)) { setMessage("Task ID 형식을 확인해 주세요.", true); return; }
  $("load-button").disabled = true;
  setMessage("승인된 이벤트를 불러오는 중입니다…");
  try {
    const response = await fetch(`/game-api/task/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "이벤트를 불러오지 못했습니다.");
    if (!Array.isArray(payload.missions) || !payload.missions.length) throw new Error("플레이 가능한 미션이 없습니다.");
    state.event = payload;
    restoreEventProgress();
    renderHud();
    renderEvent();
    save();
    setMessage("이벤트가 게임에 적용됐습니다. 모험 시작에서 미션을 플레이해 보세요!");
  } catch (error) { setMessage(error.message || "연결 오류가 발생했습니다.", true); }
  finally { $("load-button").disabled = false; }
});
