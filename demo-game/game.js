const $ = (id) => document.getElementById(id);
const state = { event: null, missions: [], index: 0, count: 0, earned: [] };

function storageKey() {
  return `opong-demo:${state.event.task_id}:${state.event.result_version}`;
}

function save() {
  if (!state.event) return;
  localStorage.setItem(storageKey(), JSON.stringify({ index: state.index, count: state.count, earned: state.earned }));
}

function loadProgress() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey()) || "null");
    if (!saved || !Number.isInteger(saved.index) || saved.index < 0 || saved.index > state.missions.length) return;
    state.index = saved.index;
    state.count = Number.isInteger(saved.count) && saved.count >= 0 ? saved.count : 0;
    state.earned = Array.isArray(saved.earned) ? saved.earned.filter(Number.isInteger) : [];
  } catch (_) {
    localStorage.removeItem(storageKey());
  }
}

function message(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}

function rewardNames(day) {
  return state.event.rewards.filter((reward) => reward.day === day)
    .map((reward) => `${reward.item_name || reward.item_id} ×${reward.quantity}`);
}

function renderDays() {
  const container = $("days");
  container.replaceChildren();
  for (let day = 1; day <= state.event.duration; day += 1) {
    const row = document.createElement("div");
    row.className = "day-row";
    const dayMissions = state.missions.filter((mission) => mission.day === day);
    const completed = dayMissions.length > 0 && dayMissions.every((mission) => state.missions.indexOf(mission) < state.index);
    const current = state.missions[state.index]?.day === day;
    if (completed) row.classList.add("done");
    if (current) row.classList.add("current");
    const label = document.createElement("span");
    label.textContent = `DAY ${String(day).padStart(2, "0")}`;
    const status = document.createElement("span");
    status.textContent = completed ? "완료 ✓" : current ? "진행 중" : dayMissions.length ? "대기" : "미션 없음";
    row.append(label, status);
    container.append(row);
  }
}

function renderRewards() {
  const container = $("reward-list");
  container.replaceChildren();
  if (!state.earned.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "미션을 완료하면 보상이 여기에 쌓입니다.";
    container.append(empty);
    return;
  }
  for (const day of [...new Set(state.earned)].sort((a, b) => a - b)) {
    for (const name of rewardNames(day)) {
      const reward = document.createElement("div");
      reward.className = "reward-row";
      reward.textContent = `🎁  ${name}`;
      container.append(reward);
    }
  }
}

function render() {
  if (!state.event) return;
  $("game").classList.remove("hidden");
  $("event-name").textContent = state.event.event_name;
  $("event-meta").textContent = `${state.event.duration}일 이벤트 · ${state.event.target_user || "전체 유저"} · 데이터 ${state.event.game_data_version || "미확인"}`;
  renderDays();
  renderRewards();
  const mission = state.missions[state.index];
  const completed = !mission;
  $("complete").classList.toggle("hidden", !completed);
  $("action-button").disabled = completed;
  $("target-button").disabled = completed;
  if (completed) {
    $("day-badge").textContent = "ALL CLEAR";
    $("progress-text").textContent = `${state.missions.length} / ${state.missions.length} 미션`;
    $("mission-name").textContent = "모든 미션 완료";
    $("mission-detail").textContent = "다른 이벤트를 불러오거나 진행도를 초기화할 수 있습니다.";
    $("progress-fill").style.width = "100%";
    $("action-button").textContent = "이벤트 완료";
    return;
  }
  const target = Math.max(1, Number(mission.target_count) || 1);
  state.count = Math.min(state.count, target - 1);
  $("day-badge").textContent = `DAY ${String(mission.day).padStart(2, "0")}`;
  $("progress-text").textContent = `${state.index + 1} / ${state.missions.length} 미션`;
  $("mission-name").textContent = mission.mission_name || mission.mission_type;
  $("mission-detail").textContent = `${mission.content_name || mission.content_id || "이벤트 콘텐츠"} · ${state.count} / ${target}회`;
  $("progress-fill").style.width = `${(state.count / target) * 100}%`;
  $("action-button").textContent = mission.mission_type === "dungeon_clear" ? "던전 클리어" : "미션 진행하기";
  $("target-button").textContent = mission.mission_type === "dungeon_clear" ? "🏰" : "👾";
}

function act() {
  const mission = state.missions[state.index];
  if (!mission) return;
  state.count += 1;
  $("hit-effect").classList.remove("pop");
  void $("hit-effect").offsetWidth;
  $("hit-effect").classList.add("pop");
  const target = Math.max(1, Number(mission.target_count) || 1);
  if (state.count >= target) {
    const day = mission.day;
    state.index += 1;
    state.count = 0;
    const dayFinished = !state.missions.slice(state.index).some((item) => item.day === day);
    if (dayFinished && !state.earned.includes(day)) {
      state.earned.push(day);
      const names = rewardNames(day);
      message(names.length ? `DAY ${day} 완료! 데모 보상: ${names.join(", ")}` : `DAY ${day} 미션 완료!`);
    } else {
      message("미션 완료! 다음 미션으로 이동합니다.");
    }
  }
  save();
  render();
}

$("load-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const taskId = $("task-id").value.trim().toUpperCase();
  if (!/^TASK-[A-Z0-9]{12}$/.test(taskId)) {
    message("Task ID는 TASK- 뒤에 영문 대문자·숫자 12자리 형식이어야 합니다.", true);
    return;
  }
  $("load-button").disabled = true;
  message("승인된 이벤트를 불러오는 중입니다…");
  try {
    const response = await fetch(`/game-api/task/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "이벤트를 불러오지 못했습니다.");
    if (!Array.isArray(payload.missions) || !payload.missions.length) throw new Error("플레이 가능한 미션이 없습니다.");
    state.event = payload;
    state.missions = [...payload.missions].sort((a, b) => a.day - b.day);
    state.index = 0;
    state.count = 0;
    state.earned = [];
    loadProgress();
    render();
    message("이벤트가 연결됐습니다. 몬스터를 누르거나 미션 버튼을 눌러 시작하세요.");
    $("game").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    state.event = null;
    $("game").classList.add("hidden");
    message(error.message || "연결 중 오류가 발생했습니다.", true);
  } finally {
    $("load-button").disabled = false;
  }
});

$("action-button").addEventListener("click", act);
$("target-button").addEventListener("click", act);
$("reset-button").addEventListener("click", () => {
  if (!state.event) return;
  localStorage.removeItem(storageKey());
  state.index = 0;
  state.count = 0;
  state.earned = [];
  render();
  message("이 브라우저의 진행도를 초기화했습니다.");
});
