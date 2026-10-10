import Phaser from "phaser";
import valley from "../../assets/sunlit-valley.png";
import arena from "../../assets/dungeon-arena.png";
import hero from "../../assets/hero-mascot.png";
import goblin from "../../assets/goblin.png";
import slime from "../../assets/slime.png";
import bat from "../../assets/stage-bat.webp";
import guardian from "../../assets/stage-guardian.webp";
import spirit from "../../assets/stage-spirit.webp";
import dragon from "../../assets/day-7-dragon.png";
import starCandySkin from "../../assets/skin-star-candy.webp";
import mushroomSkin from "../../assets/skin-mushroom.webp";
import moonBunnySkin from "../../assets/skin-moon-bunny.webp";
import trainingCourtyard from "../../assets/training-courtyard-v1.png";
import royalGallery from "../../assets/royal-gallery-v1.png";
import moonlightBoutique from "../../assets/moonlight-boutique-v1.png";
import mineSootRat from "../../assets/mine-soot-rat-v2.png";
import mineVeinBeetle from "../../assets/mine-vein-beetle-v2.png";
import mineCrystalMimic from "../../assets/mine-crystal-mimic-v2.png";
import mineStarveinDragon from "../../assets/mine-starvein-dragon-v2.png";

export class BootScene extends Phaser.Scene {
  constructor() { super("BootScene"); }

  preload() {
    this.load.image("valley", valley);
    this.load.image("arena", arena);
    this.load.image("training-courtyard", trainingCourtyard);
    this.load.image("royal-gallery", royalGallery);
    this.load.image("moonlight-boutique", moonlightBoutique);
    this.load.image("hero", hero);
    this.load.image("goblin", goblin);
    this.load.image("slime", slime);
    this.load.image("bat", bat);
    this.load.image("guardian", guardian);
    this.load.image("spirit", spirit);
    this.load.image("dragon", dragon);
    this.load.image("skin-star-candy", starCandySkin);
    this.load.image("skin-mushroom", mushroomSkin);
    this.load.image("skin-moon-bunny", moonBunnySkin);
    this.load.image("mine-soot-rat", mineSootRat);
    this.load.image("mine-vein-beetle", mineVeinBeetle);
    this.load.image("mine-crystal-mimic", mineCrystalMimic);
    this.load.image("mine-starvein-dragon", mineStarveinDragon);
  }

  create() {
    this.scene.start("LobbyScene");
  }
}
