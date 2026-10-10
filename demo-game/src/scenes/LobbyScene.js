import Phaser from "phaser";

const STAGE_MONSTER_NAMES = ["초원 슬라임", "숲 고블린", "수정 박쥐", "폐광 수호자", "달빛 정령"];
export class LobbyScene extends Phaser.Scene {
  constructor() { super("LobbyScene"); }

  create(data = {}) {
    this.drawWorld(data.mode || "lobby", data.user || null, data.stage || null);
  }

  drawWorld(mode, user = null, stage = null) {
    this.tweens.killAll();
    this.children.removeAll();
    const special = mode === "special";
    const dungeon = mode === "dungeon";
    const isStage = mode === "stage";
    const growth = mode === "growth";
    const collection = mode === "collection";
    const shop = mode === "shop";
    const background = growth ? "training-courtyard" : collection ? "royal-gallery" : shop ? "moonlight-boutique" : special || dungeon ? "arena" : "valley";
    const image = this.add.image(480, 270, background);
    const scale = Math.max(960 / image.width, 540 / image.height);
    image.setScale(scale).setAlpha(1).setDepth(0);
    const shade = this.add.rectangle(480, 270, 960, 540, 0x101824, 0.08).setDepth(1);

    const titles = {
      lobby: "오퐁 왕국의 거점", growth: "달빛 훈련장", stage: "달빛 초원 · 스테이지",
      dungeon: "별무리 폐광", special: "월광 균열", collection: "원정대 보관함", shop: "왕국 꾸미기 상점",
    };
    const subtitles = {
      lobby: "OPONG KINGDOM · ADVENTURE FRONTIER", growth: "TRAINING GROUNDS · HERO GROWTH",
      stage: "WORLD " + String(stage?.order || 1).padStart(2, "0") + " · " + (stage?.name || "MOONLIT MEADOW"),
      dungeon: "DAILY DUNGEON · STARDUST MINE", special: "EVENT DUNGEON · THE SEALED RIFT",
      collection: "TREASURE VAULT · YOUR COLLECTION", shop: "KINGDOM BOUTIQUE · COSTUMES & GOODS",
    };
    this.add.text(44, 34, titles[mode] || titles.lobby, { fontFamily: "serif", fontSize: "29px", color: "#fff1d2", stroke: "#17202b", strokeThickness: 4 }).setShadow(0, 3, "#111827", 6, false, true);
    this.add.text(48, 74, subtitles[mode] || subtitles.lobby, { fontFamily: "sans-serif", fontSize: "11px", color: "#e0c995", letterSpacing: 2 });
    this.add.rectangle(480, 476, 960, 128, 0x0a111a, 0.4);

    const skinTextures = { SKIN_STAR_CANDY: "skin-star-candy", SKIN_MUSHROOM: "skin-mushroom", SKIN_MOON_BUNNY: "skin-moon-bunny" };
    const hero = this.add.image(growth ? 335 : collection || shop ? 270 : 290, 366, skinTextures[user?.equipped_skin_id] || "hero").setScale(0.17);
    const visualSlot = stage?.monster?.visual_slot || 1;
    const monsterName = stage ? STAGE_MONSTER_NAMES[(visualSlot - 1) % STAGE_MONSTER_NAMES.length] : "초원 슬라임";
    const stageEnemyKeys = ["slime", "goblin", "bat", "guardian", "spirit"];
    let enemies = [];

    if (growth || collection || shop) {
      this.tweens.add({ targets: hero, y: 356, duration: 1450, yoyo: true, repeat: -1, ease: "Sine.easeInOut" });
    } else {
      this.add.ellipse(290, 420, 178, 24, 0x05090e, 0.5);
      this.add.ellipse(714, 420, 160, 22, 0x05090e, 0.5);
      if (dungeon) {
        enemies = [
          this.add.image(610, 351, "mine-soot-rat").setScale(0.095),
          this.add.image(720, 344, "mine-vein-beetle").setScale(0.095),
          this.add.image(830, 351, "mine-crystal-mimic").setScale(0.095),
        ];
      } else if (isStage || special) {
        enemies = [this.add.image(714, 354, special ? "dragon" : stageEnemyKeys[(visualSlot - 1) % 5]).setScale(special ? 0.22 : 0.105)];
      }
      this.tweens.add({ targets: hero, y: 344, duration: 1250, yoyo: true, repeat: -1, ease: "Sine.easeInOut" });
      enemies.forEach((enemy, index) => this.tweens.add({ targets: enemy, y: enemy.y - 9, angle: -3, delay: index * 140, duration: 920, yoyo: true, repeat: -1, ease: "Sine.easeInOut" }));
      if (isStage || dungeon || special) {
        this.add.rectangle(722, 137, 184, 42, 0x111a23, 0.84).setStrokeStyle(1, special ? 0x9f87dc : dungeon ? 0x8bbbab : 0xc87970, 0.55);
        this.add.text(648, 123, special ? "월광 균열의 수호자" : dungeon ? "폐광 파티 · 적 3마리" : monsterName, { fontSize: dungeon ? "12px" : "14px", color: "#ffe8d2", fontStyle: "bold" });
        this.add.text(648, 143, isStage && stage ? stage.order + "구역 · " + (user?.hunt_mode === "farm" ? "최고 구역 반복" : "다음 관문") : dungeon ? "1명 대 3명 · 5층 보스" : "도전 대기 중", { fontSize: "9px", color: "#c1b69d" });
        this.add.rectangle(720, 189, 142, 7, 0x25222a, 0.95).setStrokeStyle(1, 0xe7c58c, 0.58);
        this.add.rectangle(720, 189, 132, 4, 0xd56770, 0.95);
      }
      if (special) this.add.circle(714, 336, 90, 0x7657d2, 0.14).setBlendMode(Phaser.BlendModes.ADD);
    }

    this.add.rectangle(growth || collection || shop ? 294 : 290, 137, 248, 48, 0x111a23, 0.84).setStrokeStyle(1, 0xd4b775, 0.5);
    this.add.text(growth || collection || shop ? 188 : 184, 119, user?.display_name || "달빛 원정대", { fontSize: "15px", color: "#fff0d0", fontStyle: "bold" });
    this.add.text(growth || collection || shop ? 188 : 184, 142, user ? "Lv." + user.level + " · ATK " + user.stats.attack + " · HP " + user.stats.max_hp : "성장한 영웅으로 다음 관문에 도전하세요", { fontSize: "9px", color: "#c1b69d" });
    const captions = {
      lobby: "원정대가 다음 모험을 기다립니다", growth: "훈련장에서 영웅의 능력을 단련하세요",
      stage: monsterName + "를 처치하고 원정대를 성장시키세요", dungeon: "폐광의 몬스터 무리가 길을 막아섭니다",
      special: "균열의 수호자가 길을 막아섭니다", collection: "모험에서 모은 코스튬과 재화를 확인하세요",
      shop: "마음에 드는 코스튬을 골라 영웅에게 선물하세요",
    };
    this.add.text(58, 509, captions[mode] || captions.lobby, { fontFamily: "sans-serif", fontSize: "16px", color: "#f7e9cb" });
    this.children.list.forEach((child) => { if (child !== image && child !== shade && child !== hero) child.setDepth?.(3); });
    hero.setDepth(5);
    enemies.forEach((enemy) => enemy.setDepth(5));
  }
}
