// 音效映射回归（对齐 Delphi 原版 SoundUtil.pas / Actor.pas 的常量与公式）。
// 跑法：node scripts/test_sfx_map.mjs    （退出码 0 = 全通过）
//
// 这些是纯函数，不依赖浏览器，所以能直接在 Node 里跑——改映射表后先跑它，
// 别等进游戏才发现解捆又变成喝药声。

import {
    SFX, itemClickSound, itemUseSound, weaponHitSound,
    weaponStruckSound, struckBodySound, footstepBase,
} from '../frontend/audio/SoundManager.mjs';

let pass = 0, fail = 0;
function eq(label, got, want) {
    const ok = Array.isArray(want) ? String(got) === String(want) : got === want;
    if (ok) { pass++; return; }
    fail++;
    console.log(`  FAIL ${label}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}
function section(name) { console.log(`\n[${name}]`); }

// ---- 常量（SoundUtil.pas:37-143）----------------------------------------
section('s_* 常量值');
eq('walk ground l', SFX.walkL, 1);
eq('run ground l', SFX.runL, 3);
eq('walk stone l', SFX.walkStoneL, 5);
eq('walk wood l', SFX.walkWoodL, 17);
eq('walk room l', SFX.walkRoomL, 25);
eq('hit_short', SFX.hitShort, 50);
eq('hit_fist', SFX.hitFist, 57);
eq('struck_short', SFX.struckShort, 60);
eq('struck_club', SFX.struckClub, 65);
eq('struck_body_sword', SFX.struckBodySword, 70);
eq('struck_body_fist', SFX.struckBodyFist, 73);
eq('struck_armor_sword', SFX.struckArmorSword, 80);
eq('struck_armor_fist', SFX.struckArmorFist, 83);
eq('eat_drug', SFX.eatDrug, 107);
eq('click_drug', SFX.clickDrug, 108);
eq('spacemove_out', SFX.spaceMoveOut, 109);
eq('spacemove_in', SFX.spaceMoveIn, 110);
eq('yedo_man', SFX.yedoMan, 130);
eq('yedo_woman', SFX.yedoWoman, 131);
eq('man_struck', SFX.manStruck, 138);
eq('wom_struck', SFX.womanStruck, 139);
eq('man_die', SFX.manDie, 144);
eq('wom_die', SFX.womanDie, 145);

// ---- ItemUseSound（SoundUtil.pas:313）：只有 0/1/2 出声 ------------------
section('使用物品音 ItemUseSound');
eq('StdMode 0 → 108', itemUseSound(0), 108);
eq('StdMode 1 → 107', itemUseSound(1), 107);
eq('StdMode 2 → 107', itemUseSound(2), 107);
eq('StdMode 3 卷轴 → 静默', itemUseSound(3), 0);
eq('StdMode 4 技能书 → 静默', itemUseSound(4), 0);
eq('StdMode 31 捆(随机传送卷包) → 静默', itemUseSound(31), 0);
eq('StdMode 15 头盔 → 静默', itemUseSound(15), 0);

// ---- ItemClickSound（SoundUtil.pas:294）---------------------------------
section('背包单击音 ItemClickSound');
eq('0 → click_drug', itemClickSound(0), 108);
eq('31 → click_drug', itemClickSound(31), 108);
eq('5 → click_weapon', itemClickSound(5), 111);
eq('10 → click_armor', itemClickSound(10), 112);
eq('15 → click_helmet', itemClickSound(15), 116);
eq('22 → click_ring', itemClickSound(22), 113);
eq('24 → click_armring', itemClickSound(24), 114);
eq('19 → click_necklace', itemClickSound(19), 115);
eq('其它 → itmclick', itemClickSound(99), 118);

// ---- 攻击武器音（Actor.pas:2273 case m_btWeapon div 2）-------------------
section('攻击武器音 weaponHitSound（入参是 shape*2+sex）');
const W = (shape) => shape * 2;   // sex=0
eq('shape 6 → hit_short', weaponHitSound(W(6)), 50);
eq('shape 20 → hit_short', weaponHitSound(W(20)), 50);
eq('shape 1 → hit_wooden', weaponHitSound(W(1)), 51);
eq('shape 2 → hit_sword', weaponHitSound(W(2)), 52);
eq('shape 5/9/13/14/22 → hit_sword', weaponHitSound(W(22)), 52);
eq('shape 4 → hit_do', weaponHitSound(W(4)), 53);
eq('shape 3 → hit_axe', weaponHitSound(W(3)), 54);
eq('shape 24 → hit_club', weaponHitSound(W(24)), 55);
eq('shape 8 → hit_long', weaponHitSound(W(8)), 56);
eq('shape 0 → hit_fist', weaponHitSound(W(0)), 57);

// ---- 受击武器音（Actor.pas:2362：原版除了两次 2，照抄）-------------------
section('受击武器音 weaponStruckSound（原版双重 div 2）');
eq('shape 1 → 0（原版无此分支）', weaponStruckSound(W(1)), 0);
eq('shape 2 → struck_wooden(61)', weaponStruckSound(W(2)), 61);
eq('shape 4 → struck_sword(62)', weaponStruckSound(W(4)), 62);
eq('shape 6 → struck_axe(64)', weaponStruckSound(W(6)), 64);
eq('shape 8 → struck_do(63)', weaponStruckSound(W(8)), 63);
eq('shape 12 → struck_short(60)', weaponStruckSound(W(12)), 60);
eq('shape 24 → struck_wooden(61)', weaponStruckSound(W(24)), 61);
eq('shape 0 → 0', weaponStruckSound(W(0)), 0);

// ---- 受击身体/护甲音（Actor.pas:2296-2309）-------------------------------
section('受击身体音 struckBodySound');
eq('轻甲 + 剑 → body_sword(70)', struckBodySound(W(2), 0, true), 70);
eq('轻甲 + 斧 → body_axe(71)', struckBodySound(W(3), 0, true), 71);
eq('轻甲 + 长 → body_longstick(72)', struckBodySound(W(8), 0, true), 72);
eq('轻甲 + 拳 → body_fist(73)', struckBodySound(W(0), 0, true), 73);
eq('重甲(dress div2=3) + 剑 → armor_sword(80)', struckBodySound(W(2), 6, true), 80);
eq('重甲 + 斧 → armor_axe(81)', struckBodySound(W(3), 6, true), 81);
eq('重甲 + 长 → armor_longstick(82)', struckBodySound(W(8), 6, true), 82);
eq('重甲 + 拳 → armor_fist(83)', struckBodySound(W(0), 6, true), 83);
eq('攻击者不是玩家 → 默认 72', struckBodySound(W(2), 6, false), 72);

// ---- 地形脚步（Actor.pas:2234-2257，本项目实现的 mid/fr 两层）------------
section('地形脚步 footstepBase(bng, mid, fr)');
eq('默认 → ground(1)', footstepBase(-1, -1, -1), 1);
eq('mid 0..115 → ground', footstepBase(-1, 0, -1), 1);
eq('mid 120..124 → lawn(9)', footstepBase(-1, 122, -1), 9);
eq('fr 221..289 → stone(5)', footstepBase(-1, -1, 250), 5);
eq('fr 583..658 → stone', footstepBase(-1, -1, 600), 5);
eq('fr 7163..7295 → stone', footstepBase(-1, -1, 7200), 5);
eq('fr 3125..3267 → wood(17)', footstepBase(-1, -1, 3200), 17);
eq('fr 6030..6999 → wood', footstepBase(-1, -1, 6500), 17);
eq('fr 3316..3589 → room(25)', footstepBase(-1, -1, 3400), 25);
eq('bng 825 起偶数段 → stone', footstepBase(825, -1, -1), 5);
eq('bng 1375 起偶数段 → cave(21)', footstepBase(1375, -1, -1), 21);

// ---- 魔法起手音（Actor.pas:2319）--------------------------------------
section('施法音公式 10000 + MagicSerial*10');
const magicId = (m) => 10000 + m * 10;
eq('火球(1)', magicId(1), 10010);
eq('雷电(11)', magicId(11), 10110);
eq('烈火(26)', magicId(26), 10260);

// ---- 怪物叫声（Actor.pas:2345-2351）------------------------------------
section('怪物叫声 Appr*10+200+偏移');
const mon = (appr, off) => appr * 10 + 200 + off;
eq('鸡(46) 攻击 +2', mon(46, 2), 662);
eq('鸡(46) 挨打 +4', mon(46, 4), 664);
eq('鸡(46) 死亡 +5', mon(46, 5), 665);
eq('稻草人(27) 死亡 +5', mon(27, 5), 475);

console.log(`\n结果: ${pass} PASS / ${fail} FAIL`);
process.exit(fail === 0 ? 0 : 1);
