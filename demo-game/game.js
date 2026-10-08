const $ = (id) => document.getElementById(id);
const HITS_PER_MONSTER = [1, 2, 3];
const MONSTERS = [
  { name: "초원 슬라임", position: "0% 0%" },
  { name: "날개눈", position: "50% 0%" },
  { name: "세눈 젤리", position: "100% 0%" },
  { name: "뿔 임프", position: "0% 100%" },
  { name: "밤날개", position: "50% 100%" },
  { name: "수정 골렘", position: "100% 100%" },
  { name: "드래곤", position: "center" },
];
const MISSION_LABELS = {
  monster_kill: "몬스터 처치",
  dungeon_clear: "던전 완료",
  level_reach: "레벨 달성",
  building_destroy: "건물 파괴",
  raid_win: "기지 점령",
  trophy_gain: "트로피 획득",
};
const state = {
  event: null, day: 1, kills: 0, gold: 0, hp: 1, position: 84,
  phase: "idle", run: 0, lockedUntil: 0, lastFrame: 0,
};

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : fallback;
}
function setTaskMessage(message, error = false) {
  $("task-message").textContent = message;
  $("task-message").classList.toggle("error", error);
}
function currentMonster() {
  return MONSTERS[state.day === 7 ? 6 : Math.min(state.day - 1, 5)];
}
function dayRewards(day) {
  const rewards = state.event.rewards.filter((reward) => Number(reward.day) === day);
  return rewards.length
    ? rewards.map((reward) => {
      const name = reward.item_name || reward.item_id || "보상";
      return String(name) + " ×" + Math.max(1, number(reward.quantity, 1));
    }).join(" · ")
    : "등록된 보상 없음";
}
function createText(tag, className, value) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = value;
  return element;
}
function renderPlan() {
  if (!state.event) return;
  const list = $("day-list");
  list.replaceChildren();
  for (let day = 1; day <= state.event.duration; day += 1) {
    const missions = state.event.missions.filter((mission) => Number(mission.day) === day);
    const completed = day < state.day || (day === state.day && state.kills === 3);
    const active = day === state.day && !completed;
    const row = document.createElement("article");
    row.className = "day-row " + (completed ? "completed" : active ? "active" : "future");
    row.append(createText("strong", "day-label", "Day " + day));
    const content = document.createElement("div");
    content.className = "day-content";
    content.append(createText("b", "day-mission", missions.length
      ? missions.map((mission) => {
        const name = mission.mission_name || MISSION_LABELS[mission.mission_type] || "미션";
        return String(name) + " · 목표 " + Math.max(1, number(mission.target_count, 1));
      }).join(" / ")
      : "등록된 미션 없음"));
    content.append(createText("small", "day-reward", "기획안 보상: " + dayRewards(day)));
    row.append(content);
    row.append(createText("span", "day-status", completed
      ? "시연 완료" : active ? state.kills + "/3 시연" : "예정"));
    list.append(row);
  }
}
function renderBattle() {
  $("battle-day").textContent = "Day " + state.day;
  $("monster-count").textContent = state.kills + " / 3";
  $("gold-count").textContent = state.gold.toLocaleString();
  $("monster-wrap").style.left = state.position + "%";
  const maxHp = HITS_PER_MONSTER[Math.min(state.kills, 2)];
  $("monster-health-fill").style.width = Math.max(0, state.hp) / maxHp * 100 + "%";
  $("monster-wrap").classList.toggle("in-range", state.phase === "approach" && state.position <= 58);
  $("attack-button").disabled = state.phase !== "approach" || Date.now() < state.lockedUntil;
}
function spawnMonster() {
  state.phase = "approach";
  state.hp = HITS_PER_MONSTER[state.kills];
  state.position = 84;
  state.lockedUntil = 0;
  const monster = currentMonster();
  const sprite = $("monster-sprite");
  const wrap = $("monster-wrap");
  wrap.hidden = false;
  wrap.classList.remove("defeated", "hit");
  wrap.style.width = (state.day === 7 ? 138 : 116) + state.kills * (state.day === 7 ? 37 : 29) + "px";
  wrap.style.height = wrap.style.width;
  sprite.style.backgroundImage = state.day === 7
    ? "url('/assets/day-7-dragon.png')"
    : "url('/assets/monster-days.png')";
  sprite.style.backgroundSize = state.day === 7 ? "contain" : "300% 200%";
  sprite.style.backgroundPosition = monster.position;
  sprite.setAttribute("aria-label", monster.name);
  $("battle-message").textContent = monster.name + "이 다가옵니다. 가까워지면 공격하세요.";
  renderBattle();
}
function finishDay() {
  state.phase = "day-complete";
  const finalDay = state.day === state.event.duration;
  $("result-kicker").textContent = finalDay ? "PLAYTEST COMPLETE" : "DAY COMPLETE";
  $("result-title").textContent = finalDay
    ? "전체 시연 완료" : "Day " + state.day + " 시연 완료";
  $("result-summary").textContent = "몬스터 3마리 처치 · 시연 골드 +" + (60 * state.day);
  $("result-reward").textContent = "기획안 보상 미리보기: " + dayRewards(state.day);
  $("next-day").textContent = finalDay ? "처음부터 다시 보기" : "다음 Day";
  $("day-result").hidden = false;
  $("battle-message").textContent = finalDay
    ? "이 이벤트의 모든 Day를 살펴봤습니다." : "다음 Day로 넘어갈 수 있습니다.";
  renderBattle();
  renderPlan();
}
function attack() {
  if (state.phase !== "approach" || Date.now() < state.lockedUntil) return;
  const run = state.run;
  state.lockedUntil = Date.now() + 420;
  $("hero-sprite").classList.remove("swing");
  void $("hero-sprite").offsetWidth;
  $("hero-sprite").classList.add("swing");
  window.setTimeout(() => {
    if (run !== state.run) return;
    $("hero-sprite").classList.remove("swing");
    renderBattle();
  }, 420);

  if (state.position > 58) {
    $("battle-message").textContent = "아직 멀리 있습니다. 몬스터가 가까이 오면 다시 공격하세요.";
    renderBattle();
    return;
  }
  state.hp -= 1;
  $("hit-effect").classList.remove("visible");
  void $("hit-effect").offsetWidth;
  $("hit-effect").classList.add("visible");
  $("monster-wrap").classList.add("hit");
  window.setTimeout(() => {
    if (run === state.run) $("monster-wrap").classList.remove("hit");
  }, 380);
  if (state.hp > 0) {
    $("battle-message").textContent = "명중! " + (HITS_PER_MONSTER[state.kills] - state.hp) + "/" + HITS_PER_MONSTER[state.kills] + "회 공격했습니다.";
    renderBattle();
    return;
  }

  state.phase = "defeated";
  const earned = 10 * state.day * (state.kills + 1);
  state.gold += earned;
  state.kills += 1;
  $("monster-wrap").classList.add("defeated");
  $("battle-message").textContent = currentMonster().name + " 처치! 시연 골드 +" + earned;
  renderBattle();
  renderPlan();
  window.setTimeout(() => {
    if (run !== state.run) return;
    if (state.kills === 3) finishDay();
    else spawnMonster();
  }, 700);
}
function startRun(event) {
  state.run += 1;
  state.event = event;
  state.day = 1;
  state.kills = 0;
  state.gold = 0;
  state.phase = "idle";
  $("event-name").textContent = event.event_name || "이벤트";
  $("event-task-id").textContent = event.task_id;
  $("event-source").textContent = event.game_source === "clash_of_clans"
    ? "Clash 공개 표본을 바탕으로 승인된 기획안 · " + event.duration + "일"
    : "승인된 이벤트 기획안 · " + event.duration + "일";
  $("start-screen").hidden = true;
  $("game-screen").hidden = false;
  $("change-task").hidden = false;
  $("day-result").hidden = true;
  spawnMonster();
  renderPlan();
  $("attack-button").focus();
  window.scrollTo(0, 0);
}
function showStart() {
  state.run += 1;
  state.phase = "idle";
  state.event = null;
  $("game-screen").hidden = true;
  $("start-screen").hidden = false;
  $("change-task").hidden = true;
  $("task-id").value = "";
  setTaskMessage("승인·규칙 검증·AI 검수를 통과한 이벤트만 불러올 수 있습니다.");
  $("task-id").focus();
}
async function loadTask(taskId) {
  if (!/^TASK-[A-Z0-9]{12}$/.test(taskId)) {
    setTaskMessage("Task ID 형식을 확인하세요. 예: TASK-123456789ABC", true);
    return;
  }
  $("load-task").disabled = true;
  setTaskMessage("승인된 이벤트를 확인하는 중입니다…");
  try {
    const response = await fetch("/game-api/task/" + encodeURIComponent(taskId), { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "이벤트를 불러오지 못했습니다.");
    if (!Array.isArray(payload.missions) || !Array.isArray(payload.rewards)
      || !Number.isInteger(payload.duration) || payload.duration < 1 || payload.duration > 7) {
      throw new Error("이벤트 기획안 형식이 올바르지 않습니다.");
    }
    startRun(payload);
  } catch (error) {
    setTaskMessage(error.message || "게임 서버에 연결할 수 없습니다.", true);
  } finally {
    $("load-task").disabled = false;
  }
}
function tick(timestamp) {
  const elapsed = state.lastFrame ? Math.min(0.05, (timestamp - state.lastFrame) / 1000) : 0;
  state.lastFrame = timestamp;
  if (state.phase === "approach" && state.position > 48) {
    state.position = Math.max(48, state.position - (10 + state.day) * elapsed);
    renderBattle();
  }
  window.requestAnimationFrame(tick);
}

$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadTask($("task-id").value.trim().toUpperCase());
});
$("attack-button").addEventListener("click", attack);
$("change-task").addEventListener("click", showStart);
$("next-day").addEventListener("click", () => {
  if (state.phase !== "day-complete") return;
  if (state.day === state.event.duration) {
    startRun(state.event);
    return;
  }
  state.day += 1;
  state.kills = 0;
  $("day-result").hidden = true;
  spawnMonster();
  renderPlan();
});
window.requestAnimationFrame(tick);
