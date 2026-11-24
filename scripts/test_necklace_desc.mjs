// 纯逻辑回归：从 PlayScene.mjs 抠 _itemDesc 函数体验证（不启浏览器）
// 用法: node scripts/test_necklace_desc.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../frontend/scene/PlayScene.mjs", import.meta.url), "utf8");
// 抠出 _itemDesc(it) { ... } 方法体
const m = src.match(/_itemDesc\(it\) \{([\s\S]*?)\n    \}\n/);
if (!m) { console.error("FAIL: 未匹配到 _itemDesc"); process.exit(1); }
// eslint-disable-next-line no-new-func
const itemDesc = new Function("it", m[1]);

const mk = (o) => Object.assign({ weight: 1, durability: 10000, durabilityMax: 10000 }, o);
let pass = 0, fail = 0;
function check(label, it, wantSub) {
    const d = itemDesc(it);
    if (d.line2.includes(wantSub)) { pass++; console.log(`ok  ${label}: ${d.line2}`); }
    else { fail++; console.log(`FAIL ${label}: line2=${JSON.stringify(d.line2)} 期望含 "${wantSub}"`); }
}
// 项链：仅 Min 非零（用户反馈不显示的一类）
check("黑檀项链 魔法下限", mk({ name: "黑檀项链", stdMode: 20, mc: 1, mcMax: 0 }), "魔法 1-0");
check("黄色水晶项链 道术下限", mk({ name: "黄色水晶项链", stdMode: 20, sc: 1, scMax: 0 }), "道术 1-0");
check("黑色水晶项链 魔法下限", mk({ name: "黑色水晶项链", stdMode: 20, mc: 1, mcMax: 0 }), "魔法 1-0");
check("魔鬼项链 魔法下限2", mk({ name: "魔鬼项链", stdMode: 20, mc: 2, mcMax: 0 }), "魔法 2-0");
check("琥珀项链 道术下限2", mk({ name: "琥珀项链", stdMode: 20, sc: 2, scMax: 0 }), "道术 2-0");
// 项链：仅 ac/mac 下限（灯笼=魔法躲避用 ac 上限，这里覆盖防御显示路径）
check("白色虎齿项链 道术下限", mk({ name: "白色虎齿项链", stdMode: 19, sc: 1, scMax: 0, ac: 0, acMax: 2 }), "道术 1-0");
// 常规：Min/Max 都有
check("记忆项链 攻击", mk({ name: "记忆项链", stdMode: 19, dc: 2, dcMax: 4 }), "攻击 2-4");
check("力量戒指 攻击上限", mk({ name: "力量戒指", stdMode: 24, dc: 0, dcMax: 2 }), "攻击 0-2");
check("防御链条 防御", mk({ name: "防御项链", stdMode: 20, ac: 1, acMax: 2 }), "防御 1-2");
// 无属性物品不应冒出属性行
{
    const d = itemDesc(mk({ name: "金项链", stdMode: 20 }));
    if (!d.line2) { pass++; console.log("ok  金项链(无属性): line2 为空"); }
    else { fail++; console.log(`FAIL 金项链: 不应有属性但 line2=${d.line2}`); }
}
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
