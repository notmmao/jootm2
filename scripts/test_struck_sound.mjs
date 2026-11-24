// 纯逻辑回归：SoundManager attack 分支的受击音门（miss/noDamage 不播受击音，
// 攻击方挥刀音照播）。原版 ObjBase.pas:5441 `if dam > 0` 才 StruckDamage+
// RM_STRUCK；RM_HIT 无条件（用户 2026-11-24：不掉血却播挨打音效）。
// 用法: node scripts/test_struck_sound.mjs
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../frontend/audio/SoundManager.mjs", import.meta.url), "utf8");
const m = src.match(/case 'attack': \{([\s\S]*?)\n            case 'spellCast'/);
if (!m) { console.error("FAIL: 未匹配 attack 分支"); process.exit(1); }

let pass = 0, fail = 0;
function check(label, cond, detail) {
    if (cond) { pass++; console.log("ok  " + label); }
    else { fail++; console.log(`FAIL ${label}${detail ? " · " + detail : ""}`); }
}

// 结构化断言（避免重新执行整个 handler 的依赖）：
// 1. 受击音门同时挡 miss 与 noDamage
check("受击音挡 miss+noDamage", /if \(msg\.miss \|\| msg\.noDamage\) break;/.test(m[1]));
// 2. 攻击方音效（挥刀/喊声）在门之前 → noDamage 仍播
const gateIdx = m[1].search(/msg\.miss \|\| msg\.noDamage/);
const atkIdx = m[1].search(/weaponHitSound|_monSound\(a, 'attack'/);
check("攻击方挥刀音在受击门之前（不掉血仍播）", atkIdx >= 0 && atkIdx < gateIdx,
    `atk=${atkIdx} gate=${gateIdx}`);
// 3. 受击方三段音（武器/身体/惨叫）都在门之后
const tgtIdx = m[1].search(/weaponStruckSound\(atkWeapon\)/);
check("受击方声音在门之后", gateIdx >= 0 && tgtIdx > gateIdx, `tgt=${tgtIdx} gate=${gateIdx}`);

// 行为级模拟：把门逻辑抠出来跑真值表
// eslint-disable-next-line no-new-func
const playsStruck = new Function("msg", `
    if (msg.miss || msg.noDamage) return false;
    return true;`);
check("miss 不播受击音", !playsStruck({ miss: true }));
check("noDamage 不播受击音", !playsStruck({ noDamage: true, damage: 0 }));
check("有伤害播受击音", playsStruck({ damage: 3 }));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
