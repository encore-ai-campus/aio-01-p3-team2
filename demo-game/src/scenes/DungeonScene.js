import Phaser from "phaser";

const ENEMY_KEYS = ["slime", "goblin", "bat", "guardian", "spirit"];
const ENEMY_NAMES = ["초원 슬라임", "숲 고블린", "수정 박쥐", "폐광 수호자", "달빛 정령"];
const MINE_TEXTURES = {
  "mine-soot-rat": "mine-soot-rat",
  "mine-vein-beetle": "mine-vein-beetle",
  "mine-crystal-mimic": "mine-crystal-mimic",
  "mine-starvein-dragon": "mine-starvein-dragon",
};

export class DungeonScene extends Phaser.Scene {
  constructor() { super("DungeonScene"); }
  init(data) { this.context = data; }

  create() {
    this.isMine = this.context.mode === "dungeon";
    this.isSpecial = this.context.mode === "special";
    this.background = this.add.image(480, 270, "arena");
    this.background.setScale(Math.max(960 / this.background.width, 540 / this.background.height))
      .setTint(this.isMine ? 0x819b91 : this.isSpecial ? 0xaba5b8 : 0xd2c8a8);
    this.add.rectangle(480, 270, 960, 540, this.isMine ? 0x091811 : 0x11131d, 0.48);
    if (this.isMine) this.drawMineDetails();
    this.add.ellipse(350, 431, 190, 30, 0x06090e, 0.48);
    this.add.ellipse(700, 431, 320, 30, 0x06090e, 0.48);
    this.title = this.add.text(42, 20, "", { fontFamily: "serif", fontSize: "25px", color: "#fff0d0" });
    this.subtitle = this.add.text(44, 53, "", { fontSize: "12px", color: "#d7cba9" });
    this.add.rectangle(250, 126, 374, 86, 0x111a23, 0.88).setStrokeStyle(1, 0xd4b775, 0.55);
    this.add.rectangle(710, 126, 374, 86, 0x111a23, 0.88).setStrokeStyle(1, 0xe7c58c, 0.6);
    this.hero = this.add.image(340, 359, this.skinTexture(this.context.user?.equipped_skin_id)).setScale(0.15).setDepth(3);
    this.heroLabel = this.add.text(78, 93, "원정대", { fontSize: "14px", color: "#fff0d8", fontStyle: "bold" });
    this.heroStats = this.add.text(78, 117, "", { fontSize: "12px", color: "#d6c8a8" });
    this.heroHpLabel = this.add.text(78, 139, "", { fontSize: "11px", color: "#a8e5bd" });
    this.add.rectangle(250, 162, 322, 10, 0x25242c, 0.98).setStrokeStyle(1, 0xd4b775, 0.45);
    this.heroHp = this.add.rectangle(90, 162, 314, 5, 0x72c997).setOrigin(0, 0.5);
    this.enemySummary = this.add.text(528, 93, "", { fontSize: "14px", color: "#fff0d8", fontStyle: "bold" });
    this.enemyStats = this.add.text(528, 117, "", { fontSize: "12px", color: "#d6c8a8" });
    this.enemyHpLabel = this.add.text(528, 139, "", { fontSize: "11px", color: "#ffb7ae" });
    this.add.rectangle(710, 162, 322, 10, 0x2b2028, 0.98).setStrokeStyle(1, 0xe7c58c, 0.65);
    this.enemyHp = this.add.rectangle(550, 162, 314, 5, 0xcf6371).setOrigin(0, 0.5);
    this.heroMaxHp = Math.max(1, this.context.user?.stats?.max_hp || 1);
    this.heroCurrentHp = this.heroMaxHp;
    this.status = this.add.text(480, 492, "전투 준비", { fontSize: "16px", color: "#f4d991" }).setOrigin(0.5);
    this.encounterLayer = this.add.container(0, 0);
    this.setEncounter(this.context.content);
  }

  drawMineDetails() {
    this.add.rectangle(480, 468, 960, 150, 0x06120f, 0.36);
    for (const [x, y, color] of [[90, 300, 0x78d5c5], [896, 264, 0x9ad4cb], [827, 410, 0x86c8ba]]) {
      this.add.polygon(x, y, [0, 40, 24, 0, 49, 38, 25, 74], color, 0.58).setStrokeStyle(2, 0xc4fbeb, 0.65);
      this.add.circle(x + 25, y + 38, 8, color, 0.35).setBlendMode(Phaser.BlendModes.ADD);
    }
    this.add.text(760, 225, "광맥 지대", { fontSize: "10px", color: "#b2ddcc" }).setOrigin(0.5);
  }

  setEncounter(content) {
    this.context.content = content;
    this.encounterLayer.removeAll(true);
    this.hero.setTexture(this.skinTexture(this.context.user?.equipped_skin_id));
    this.hero.setPosition(340, 359).setAlpha(1).setAngle(0).setScale(0.15).clearTint();
    this.heroMaxHp = Math.max(1, this.context.user?.stats?.max_hp || 1);
    this.heroCurrentHp = this.heroMaxHp;
    const stats = this.context.user?.stats || {};
    this.heroLabel.setText(this.context.user?.display_name || "원정대");
    this.heroStats.setText(`Lv. ${this.context.user?.level || 1}  ·  ATK ${stats.attack || 0}  ·  DEF ${stats.defense || 0}`);
    const isBoss = Boolean(content.is_boss);
    this.title.setText(this.isSpecial ? "월광 균열 · 심연으로" : this.isMine ? "별무리 폐광 · 파티 전투" : "원정 · 스테이지 도전");
    this.subtitle.setText(
      this.isSpecial
        ? `${content.name} · 이벤트 공간`
        : `${content.name} · ${this.isMine ? `${content.order}층` : content.order ? `${content.order}구역` : "보스전"}`,
    );
    if (this.isMine && Array.isArray(content.enemies)) {
      this.createMineParty(content.enemies, isBoss);
    } else {
      this.createSingleEnemy(content, isBoss);
    }
    this.renderVitals();
    this.status.setText("전투 준비");
  }

  createSingleEnemy(content, isBoss) {
    const slot = content.monster?.visual_slot || 1;
    const enemyKey = isBoss ? "dragon" : ENEMY_KEYS[(slot - 1) % ENEMY_KEYS.length];
    const enemyName = isBoss ? (content.monster?.name || content.name) : ENEMY_NAMES[(slot - 1) % ENEMY_NAMES.length];
    this.enemySummary.setText(enemyName);
    this.enemyStats.setText(`ATK ${content.combat?.monster_attack || 0}  ·  DEF ${content.combat?.monster_defense || 0}`);
    this.enemySprite = this.add.image(700, 359, enemyKey).setScale(isBoss ? 0.22 : 0.11).setDepth(3);
    this.encounterLayer.add(this.enemySprite);
    this.enemyMaxHp = Math.max(1, content.combat?.monster_hp || 1);
    this.enemyCurrentHp = this.enemyMaxHp;
    this.enemies = new Map([["monster", { sprite: this.enemySprite, maxHp: this.enemyMaxHp, hp: this.enemyCurrentHp }]]);
    this.partyBars = [];
    this.activeParty = false;
  }

  createMineParty(enemies, isBossFloor) {
    this.activeParty = true;
    this.enemies = new Map();
    this.partyBars = [];
    const normalPositions = [550, 710, 870];
    const escortPositions = [520, 880];
    let escortIndex = 0;
    this.enemySummary.setText(isBossFloor ? "별무리 광맥룡 · 호위 2" : `폐광 몬스터 ${enemies.length}마리`);
    this.enemyStats.setText("영웅은 앞선 적부터 공격 · 적은 한 명씩 반격");
    enemies.forEach((enemy, index) => {
      const x = enemy.role === "boss"
        ? 700
        : isBossFloor
          ? escortPositions[escortIndex++]
          : normalPositions[index];
      const texture = MINE_TEXTURES[enemy.visual_asset_id] || "mine-crystal-mimic";
      const isBoss = enemy.role === "boss";
      const scale = isBoss ? 0.22 : 0.095;
      const sprite = this.add.image(x, isBoss ? 350 : isBossFloor ? 382 : 368, texture).setScale(scale).setDepth(3);
      const card = this.add.rectangle(x, 267, 108, 58, 0x111a23, 0.86).setStrokeStyle(1, enemy.role === "boss" ? 0xe6bd69 : 0x82b5a9, 0.62);
      const name = this.add.text(x, 247, enemy.name, { fontSize: enemy.name.length > 8 ? "9px" : "10px", color: enemy.role === "boss" ? "#ffe2a0" : "#e4eadf", fontStyle: "bold" }).setOrigin(0.5);
      const role = this.add.text(x, 261, enemy.role === "boss" ? "BOSS" : "호위", { fontSize: "8px", color: "#b6c8bd" }).setOrigin(0.5);
      const hpBack = this.add.rectangle(x, 282, 88, 7, 0x322a31).setOrigin(0.5);
      const hpFill = this.add.rectangle(x - 43, 282, 84, 4, enemy.role === "boss" ? 0xe4a958 : 0x8bc7a7).setOrigin(0, 0.5);
      const hpLabel = this.add.text(x, 296, `HP ${enemy.hp} / ${enemy.hp}`, { fontSize: "8px", color: "#d8dfd6" }).setOrigin(0.5);
      this.encounterLayer.add([sprite, card, name, role, hpBack, hpFill, hpLabel]);
      const view = { sprite, card, name, role, hpBack, hpFill, hpLabel, maxHp: enemy.hp, hp: enemy.hp, enemy };
      this.enemies.set(enemy.enemy_id, view);
      this.partyBars.push(view);
    });
    const partyMax = enemies.reduce((sum, enemy) => sum + enemy.hp, 0);
    this.enemyMaxHp = partyMax;
    this.enemyCurrentHp = partyMax;
  }

  resolve(result) {
    const combat = result.combat;
    if (!combat?.combat_log) {
      this.status.setText(result.attempt.result === "succeeded" ? "승리" : "패배");
      return Promise.resolve();
    }
    this.heroMaxHp = Math.max(1, combat.hero_hp_start);
    this.heroCurrentHp = this.heroMaxHp;
    if (this.activeParty) {
      for (const enemy of combat.enemies || []) {
        const view = this.enemies.get(enemy.enemy_id);
        if (view) view.hp = enemy.hp_start;
      }
      this.enemyMaxHp = (combat.enemies || []).reduce((sum, enemy) => sum + enemy.hp_start, 0);
      this.enemyCurrentHp = this.enemyMaxHp;
    } else {
      const monsterMax = Math.max(1, combat.monster_hp_start);
      this.enemyMaxHp = monsterMax;
      this.enemyCurrentHp = monsterMax;
      const view = this.enemies.get("monster");
      if (view) { view.maxHp = monsterMax; view.hp = monsterMax; }
    }
    this.renderVitals();
    this.status.setText("전투 중");
    this.cameras.main.shake(100, 0.003);
    return new Promise((resolve) => {
      let index = 0;
      const step = () => {
        if (index >= combat.combat_log.length) {
          this.heroCurrentHp = combat.hero_hp_after;
          if (this.activeParty) {
            for (const enemy of combat.enemies || []) {
              const view = this.enemies.get(enemy.enemy_id);
              if (view) view.hp = enemy.hp_after;
            }
            this.enemyCurrentHp = (combat.enemies || []).reduce((sum, enemy) => sum + enemy.hp_after, 0);
          } else {
            this.enemyCurrentHp = combat.monster_hp_after;
            const view = this.enemies.get("monster");
            if (view) view.hp = combat.monster_hp_after;
          }
          this.renderVitals();
          const won = combat.result === "succeeded";
          this.status.setText(
            won ? "승리 · 보상 확정" : this.isMine ? "패배 · 최고 층으로 복귀" : "패배 · 성장 후 재도전",
          );
          if (won) {
            for (const view of this.enemies.values()) {
              this.tweens.add({ targets: view.sprite, alpha: 0.15, scale: view.sprite.scale * 0.7, duration: 260 });
            }
            this.cameras.main.shake(180, 0.008);
          } else {
            this.hero.setTint(0xb76b71);
            this.cameras.main.shake(240, 0.01);
          }
          this.time.delayedCall(420, resolve);
          return;
        }
        const hit = combat.combat_log[index++];
        const heroAttack = hit.actor === "hero";
        const actorView = heroAttack ? null : this.enemies.get(hit.actor_id || "monster");
        const targetView = heroAttack ? this.enemies.get(hit.target_id || "monster") : null;
        const attacker = heroAttack ? this.hero : actorView?.sprite || this.enemySprite;
        const target = heroAttack ? targetView?.sprite || this.enemySprite : this.hero;
        if (hit.target === "hero") {
          this.heroCurrentHp = hit.target_hp_after;
        } else if (this.activeParty && targetView) {
          targetView.hp = hit.target_hp_after;
        } else {
          this.enemyCurrentHp = hit.target_hp_after;
          const singleEnemy = this.enemies.get("monster");
          if (singleEnemy) singleEnemy.hp = hit.target_hp_after;
        }
        this.renderVitals();
        this.tweens.add({
          targets: attacker,
          x: attacker.x + (heroAttack ? 70 : -62),
          duration: 130,
          yoyo: true,
          ease: "Cubic.easeOut",
          onYoyo: () => {
            target?.setTintFill(0xffe2a4);
            this.time.delayedCall(80, () => target?.clearTint());
            const damage = this.add.text(target?.x || 480, (target?.y || 350) - 82, "-" + hit.damage, {
              fontSize: "22px", fontStyle: "bold", color: heroAttack ? "#ffe18a" : "#ff9d94",
              stroke: "#241b20", strokeThickness: 4,
            }).setOrigin(0.5).setDepth(8);
            this.tweens.add({ targets: damage, y: damage.y - 42, alpha: 0, duration: 520, onComplete: () => damage.destroy() });
          },
          onComplete: () => this.time.delayedCall(90, step),
        });
      };
      step();
    });
  }

  skinTexture(skinId) {
    return ({ SKIN_STAR_CANDY: "skin-star-candy", SKIN_MUSHROOM: "skin-mushroom", SKIN_MOON_BUNNY: "skin-moon-bunny" })[skinId] || "hero";
  }

  renderVitals() {
    if (this.heroHp) {
      this.heroHp.width = Math.max(0, 314 * this.heroCurrentHp / this.heroMaxHp);
      this.heroHpLabel.setText(`HP ${this.heroCurrentHp} / ${this.heroMaxHp}`);
    }
    if (this.activeParty) {
      this.enemyHpLabel.setText(`${this.enemiesDefeatedLabel()} · 남은 파티 HP ${this.enemyCurrentHp} / ${this.enemyMaxHp}`);
      this.enemyHp.width = Math.max(0, 314 * this.enemyCurrentHp / this.enemyMaxHp);
      for (const view of this.partyBars) {
        view.hpFill.width = Math.max(0, 84 * view.hp / view.maxHp);
        view.hpLabel.setText(`HP ${view.hp} / ${view.maxHp}`);
        view.sprite.setAlpha(view.hp > 0 ? 1 : 0.28);
      }
    } else if (this.enemyHp) {
      this.enemyHp.width = Math.max(0, 314 * this.enemyCurrentHp / this.enemyMaxHp);
      this.enemyHpLabel.setText(`HP ${this.enemyCurrentHp} / ${this.enemyMaxHp}`);
    }
  }

  enemiesDefeatedLabel() {
    const defeated = this.partyBars.filter((view) => view.hp <= 0).length;
    return `${defeated}/${this.partyBars.length} 처치`;
  }
}
