// 纯逻辑回归：从 PlayScene.mjs 抠 death 分支的怪物段表判定，验证
// 「所有怪物当场死亡播 ActDie 倒地动画，无多帧 die 段的定格尸体」。
// 原版依据：TCreature.Die（ObjBase.pas:2800）无条件 RM_DEATH lparam3=1
// → SM_NOWDEATH（:12583）→ 客户端 Actor.pas:1044-1050 完整播 ActDie。
// 用法: node scripts/test_monster_death_anim.mjs
import { readFileSync } from "node:fs";
import * as A from "../frontend/actor/actorData.mjs";

const src = readFileSync(new URL("../frontend/scene/PlayScene.mjs", import.meta.url), "utf8");
// 抠怪物 die/death 判定段（else if (e.type !== 'player') 分支体）
const m = src.match(/const segs = A\.raceByPM\(e\.raceImg\);([\s\S]*?)\n                    \} else \{/);
if (!m) { console.error("FAIL: 未匹配到 death 分支段表判定"); process.exit(1); }
const body = "const segs = A.raceByPM(e.raceImg);" + m[1];
// Date.now() 归零，返回 {action, dur}
// eslint-disable-next-line no-new-func
const decide = new Function("A", "e", `
    e._action = e._action || '';
    e._actionAt = 0; e._dieUntil = -1;
    ${body.replace(/Date\.now\(\)/g, "0")}
    return { action: e._action, dur: e._dieUntil - e._actionAt };
`);

let pass = 0, fail = 0;
function check(label, raceImg, wantAction, wantDur) {
    const r = decide(A, { type: 'monster', raceImg });
    const ok = r.action === wantAction && (wantDur === null || r.dur === wantDur);
    if (ok) { pass++; console.log(`ok  ${label}: ${r.action}${wantDur !== null ? ' ' + r.dur + 'ms' : ''}`); }
    else { fail++; console.log(`FAIL ${label}: got ${r.action} ${r.dur}ms, want ${wantAction} ${wantDur}ms`); }
}

// 稻草人 raceImg 18 → MA14.die 10 帧 ×120ms（原本就播，验证不回退）
check("稻草人(18) 播倒地", 18, 'die', 10 * 120);
// 骷髅 raceImg 11 → MA11.die 10 帧 ×140ms
check("骷髅(11) 播倒地", 11, 'die', 10 * 140);
// 僵尸 raceImg 17 → MA14.die 10×120
check("僵尸(17) 播倒地", 17, 'die', 10 * 120);
// 鸡/鹿/羊 51/52 → MA19.die 10 帧 ×140ms；狼 53 同
check("鸡/鹿(51/52) 播倒地", 52, 'die', 10 * 140);
// 沃玛/祖玛系 60~69 → MA33/MA19，65 → MA19.die 10×140
check("沃玛教子类(65) 播倒地", 65, 'die', 10 * 140);
// 半兽战士 raceImg 12 → MA12.die 4 帧 ×160ms
check("半兽战士(12) 播倒地", 12, 'die', 4 * 160);
// 食人花 85 → raceByPM(85) 无表（RACE_ACTION 没 85）→ 定格
check("未映射 raceImg(85) 定格", 85, 'death', 0);
// MA9（raceImg 9）die frame=1（单帧定格怪）→ 无动画可播 → 定格
check("MA9(9) 单帧 die 段定格", 9, 'death', 0);
// MA10（raceImg 10）die 4×140 → 播
check("MA10(10) 播倒地", 10, 'die', 4 * 140);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
