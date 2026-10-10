import Phaser from "phaser";

export class ResultScene extends Phaser.Scene {
  constructor() { super("ResultScene"); }

  init(data) { this.context = data; }

  create() {
    const result = this.context.result;
    const success = result.attempt.result === "succeeded";
    const bg = this.add.image(480, 270, this.context.mode === "special" ? "arena" : "valley");
    bg.setScale(Math.max(960 / bg.width, 540 / bg.height)).setAlpha(0.42);
    this.add.rectangle(480, 270, 960, 540, 0x111725, 0.55);
    const seal = this.add.circle(480, 225, 82, success ? 0xd8ae63 : 0x6a6474, 0.16).setStrokeStyle(2, success ? 0xf4d48c : 0xb3a7b4, 0.82);
    this.tweens.add({ targets: seal, scale: 1.16, alpha: 0.42, duration: 620, yoyo: true, repeat: 1, ease: "Sine.easeInOut" });
    this.add.text(480, 205, success ? "✦" : "↻", { fontSize: "58px", color: success ? "#ffe2a0" : "#d1c1bb" }).setOrigin(0.5);
    this.add.text(480, 337, success ? "도전 성공" : "다시 도전할 수 있어요", { fontFamily: "serif", fontSize: "34px", color: "#fff0d2" }).setOrigin(0.5);
    const detail = success ? `서버 확정 · ${result.experience_gained} EXP 획득` : "도전 기록이 저장되었습니다 · 성장 후 다시 시도하세요";
    this.add.text(480, 378, detail, { fontSize: "15px", color: "#d8ceb9" }).setOrigin(0.5);
    this.add.text(480, 430, `ACTION  ${result.attempt.attempt_id.slice(0, 8).toUpperCase()}  ·  ${result.attempt.reason_code}`, { fontSize: "10px", color: "#b4a88e" }).setOrigin(0.5);
  }
}
