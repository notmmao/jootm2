#!/usr/bin/env node
/** 名字显示风格离线单测（Node，无浏览器依赖）。
 *  验证 _drawHeadHud 在 'original'（原版，默认）/'current'（当前改良）下：
 *   - 原版：选中才显示名字、无 Lv、名字在躯干(anchorY-17)、血条带数字、精英绿名
 *   - 当前：全部显示、怪物带 Lv、名字在头顶(anchorY-71)、血条无数字
 */
globalThis.localStorage = { getItem() { return null; }, setItem() {} };
globalThis.document = { createElement() { return { getContext() { return {}; } }; } };

const { MapActor } = await import('../frontend/map/MapActor.mjs');

const AX = 100, AY = 300;   // anchorX/anchorY
const actor = new MapActor(800, 600);

function capture(entity, nameStyle, hudExtra = {}) {
    actor.hudMode = { enhanced: false, showHpLabel: true, hoverNamesOnly: false, nameStyle, ...hudExtra };
    const texts = [];       // {text, x, y, fill}
    const rects = [];
    let fill = null;
    const ctx = {
        set font(v) {}, set textAlign(v) {}, set textBaseline(v) {},
        set fillStyle(v) { fill = v; }, get fillStyle() { return fill; },
        set strokeStyle(v) {}, set lineWidth(v) {},
        fillText(t, x, y) { texts.push({ text: t, x, y, fill }); },
        strokeText(t, x, y) { texts.push({ text: t, x, y, fill, stroke: true }); },
        fillRect(x, y, w, h) { rects.push({ x, y, w, h }); },
        strokeRect() {}, measureText(t) { return { width: t.length * 6 }; },
    };
    actor._drawHeadHud(ctx, entity, AX, AY);
    return { texts, rects };
}

const baseMonster = {
    name: '稻草人', type: 'monster', level: 7, hp: 30, maxHp: 50,
    isFocus: true, isSelf: false,
};

let pass = 0, fail = 0;
function check(name, cond, detail = '') {
    if (cond) { pass++; console.log(`  PASS  ${name}`); }
    else { fail++; console.log(`  FAIL  ${name}${detail ? ' · ' + detail : ''}`); }
}

// ---- 原版风格 ----
{
    const { texts } = capture(baseMonster, 'original');
    const name = texts.find(t => t.text.includes('稻草人'));
    check('原版·选中怪物显示名字', !!name);
    check('原版·名字无 Lv 等级', name && !name.text.includes('Lv'), name?.text);
    check('原版·名字在躯干(anchorY-17)', name && Math.abs(name.y - (AY - 17)) < 0.01, `y=${name?.y}`);
    check('原版·血条带数字(30/50)', texts.some(t => t.text === '30/50'), texts.map(t => t.text).join(','));
}
{
    const notFocus = { ...baseMonster, isFocus: false };
    const { texts, rects } = capture(notFocus, 'original');
    check('原版·非选中怪物不显示名字', !texts.some(t => t.text.includes('稻草人')));
    // 用户 2026-11-23：怪物永远显示血条（不依赖选中）
    check('原版·非选中怪物仍显示血条', rects.some(r => r.h === 6), JSON.stringify(rects));
}
{
    const npc = { name: '比奇武器店', type: 'npc', level: 0, hp: 100, maxHp: 100, isFocus: false, isSelf: false };
    const { texts, rects } = capture(npc, 'original');
    check('原版·NPC 常显名字', texts.some(t => t.text === '比奇武器店'));
    check('原版·NPC 不显示血条', !rects.some(r => r.h === 6), JSON.stringify(rects));
}
{
    const elite = { ...baseMonster, elite: true };
    const { texts } = capture(elite, 'original');
    const name = texts.find(t => t.text.includes('稻草人'));
    check('原版·精英怪名字带前缀', name && name.text.startsWith('精英·'), name?.text);
    check('原版·精英怪名字绿色', name && name.fill === '#5ef08a', name?.fill);
}
{
    const self = { ...baseMonster, name: 'TestHero', type: 'player', isSelf: true, isFocus: false, level: 0 };
    const { texts } = capture(self, 'original');
    check('原版·自己名字始终显示', texts.some(t => t.text === 'TestHero'));
}

// ---- 当前改良风格 ----
{
    const { texts } = capture(baseMonster, 'current');
    const name = texts.find(t => t.text.includes('稻草人'));
    check('当前·怪物名字带 Lv', name && name.text.includes('Lv7'), name?.text);
    check('当前·名字在头顶(anchorY-71)', name && Math.abs(name.y - (AY - 71)) < 0.01, `y=${name?.y}`);
    check('当前·血条无数字', !texts.some(t => t.text === '30/50'), texts.map(t => t.text).join(','));
}
{
    const notFocus = { ...baseMonster, isFocus: false };
    const { texts } = capture(notFocus, 'current');
    check('当前·非选中怪物仍显示名字', texts.some(t => t.text.includes('稻草人')));
}

console.log(`\n结果: ${pass} PASS / ${fail} FAIL`);
process.exit(fail ? 1 : 0);
