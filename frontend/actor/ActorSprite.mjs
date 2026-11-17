// 实体精灵：根据实体快照 + 动作状态（_action/_actionAt）计算当前帧图索引，并懒加载纹理。
// 对应 Delphi 客户端 TActor/THumActor 的帧计算（Actor.pas）。
// 渲染层按绘制顺序返回（武器→身体→头发，后者覆盖前者）。

import { getLib } from '../image/Images.mjs';
import * as A from './actorData.mjs';

// 动作名 → 动作表段名（玩家用 HA，怪物/NPC 用 MA）
// 施法（spell）映射到 HA.spell 段（start 392, 6 帧×60ms），与普通攻击 hit 段不同（原版 ActSpell）
// 冲撞（rush）映射到 run 段（原版 ActRushLeft/Right 即 run 段 128 起的 3 帧子段，见 Actor.pas）
// 玩家死亡定格：原版 SM_DEATH（Actor.pas:1036-1043）把 startframe 设成
// endframe，即别人看到玩家死时**直接定格 ActDie 末帧**，不播倒地动画；
// 只有 SM_NOWDEATH（自己死，:1044-1050 / :2759-2765）才播完整动画。
// 没有 death→die 映射时，_action='death' 会走 `|| 'stand'` 回退成站立帧，
// 就是「人被打死后仍站着」的根因。
const PLAYER_SEG = { stand: 'stand', walk: 'walk', run: 'run', rush: 'run', attack: 'hit', spell: 'spell', struck: 'struck', die: 'die', death: 'die', sitdown: 'sitdown' };
const MON_SEG = { stand: 'stand', walk: 'walk', attack: 'attack', struck: 'struck', die: 'die', death: 'death', digup: 'death', alive: 'death' };

export class ActorSprite {
    constructor(entity) {
        this.entity = entity;
        // 站立帧动画相位（各实体错开，避免同帧同步）
        this._phase = ((entity.id || 0) % 1000) * 37;
        this._tex = new Map();      // 'lib/index' -> {canvas,px,py} 或 null
        this._loading = new Set();  // 已触发加载的 key（防重复）
        this._lastByLib = new Map();  // lib -> 最近成功纹理（当前帧未就绪时回退，避免人物消失一帧）
        this._lastLayers = null;      // 上一帧完整图层，纹理全缺时整体回退
    }

    /**
     * 预取尸体静止帧（ActDeath.start + dir）。死亡动画与尸体帧在 WIL 里相隔
     * 数百张图，_texture 只顺带预取后续 6 帧，倒地动画结束时尸体帧往往还没到，
     * getLayers 会回退到 _lastByLib 的**站立帧** → 看起来像「怪还站着、没有尸体」。
     */
    prefetchDeathFrame(e) {
        if (!e) return;
        if (e.type === 'player') {
            // 玩家尸体定格帧 = THumActor.GetDefaultFrame（Actor.pas:2781-2782）：
            // HA.ActDie.start + dir*(frame+skip) + (frame-1)。玩家是分层绘制，
            // hum/weapon/hair 各库都要预取同一 cf，否则后进视野直接看到尸体时
            // 末帧未加载 → getLayers 回退 _lastByLib 的站立帧（又「站着」）。
            const die = A.HA.die;
            const cf = die.start + (e.dir || 0) * (die.frame + die.skip) + (die.frame - 1);
            const dress = e.dress != null ? e.dress : e.sex;
            const weapon = e.weapon != null ? e.weapon : 0;
            const sex = e.sex || 0;
            this._texture('hum', A.HUMANFRAME * dress + cf);
            if (weapon >= 2) this._texture('weapon', A.HUMANFRAME * weapon + cf);
            const hair = Math.min(Math.max(e.hair | 0, 0), A.HAIR_STYLES - 1) * 2;
            if (hair > 1) this._texture('hair', A.HUMANFRAME * (hair + sex) + cf);
            return;
        }
        const segs = e.type === 'npc' ? A.npcAction(e.appr) : A.raceByPM(e.raceImg);
        const dieSeg = segs && (segs.die || segs.death);
        const deathSeg = segs && (segs.death || segs.die);
        if (!dieSeg || !deathSeg) return;
        // 原版 Actor.pas:2124 GetDefaultFrame：
        //   骷髅 → ActDeath.start（不带方向）；普通尸体 → ActDie 末帧（带方向）
        const cf = e.skeleton
            ? deathSeg.start
            : dieSeg.start + (e.dir || 0) * (dieSeg.frame + dieSeg.skip) + (dieSeg.frame - 1);
        const offset = e.type === 'npc' ? A.getNpcOffset(e.appr) : A.getMonOffset(e.appr);
        const lib = e.type === 'npc' ? 'npc' : A.getMonImgLib(e.appr);
        this._texture(lib, offset + cf);
    }

    /** 返回当前帧的绘制层 [{canvas,px,py}]；未加载的层用上一帧纹理回退，绝不整人消失。 */
    getLayers(now) {
        const spec = this._frameSpec(now);
        if (!spec) return this._lastLayers;
        const out = [];
        for (const f of spec) {
            // 叠加特效层（火焰等）没有「上一帧回退」的意义：退到身体纹理会出现怪影，宁可不画
            const tex = this._texture(f.lib, f.index) || (f.blend ? null : this._lastByLib.get(f.lib));
            if (tex) out.push({ canvas: tex.canvas, px: tex.px, py: tex.py, blend: f.blend });
        }
        if (!out.length) return this._lastLayers;
        this._lastLayers = out;
        return out;
    }

    // 计算当前帧（各层 {lib,index}），按绘制顺序
    _frameSpec(now) {
        const e = this.entity;
        const dir = e.dir || 0;
        const action = e._action || 'stand';
        const at = e._actionAt || 0;

        let segs, segKey;
        if (e.type === 'player') {
            segs = A.HA;
            segKey = PLAYER_SEG[action] || 'stand';
        } else if (e.type === 'npc') {
            segs = A.npcAction(e.appr);
            segKey = MON_SEG[action] || 'stand';
        } else {
            segs = A.raceByPM(e.raceImg);
            segKey = MON_SEG[action] || 'stand';
        }
        if (!segs) return null;

        // 尸体静止帧（原版 Actor.pas:2124 GetDefaultFrame）：
        //   骷髅 → ActDeath.start（**不带方向**，骨头架子就一张）；
        //   普通尸体 → ActDie 最后一帧 = ActDie.start + dir*(frame+skip) + (frame-1)。
        // 旧实现两种都用 ActDeath.start + dir：多数怪恰好也像尸体，但稻草人
        // （appr 27 / mon3 2520 起）的 ActDeath 段其实是**死亡火焰特效帧**
        // （DEATHFIREEFFECTBASE=2860 与 2520+340 重合），于是尸体被画成一团火。
        if (action === 'death' && e.type !== 'player') {
            const dieSeg = segs.die || segs.death || segs.stand;
            const deathSeg = segs.death || dieSeg;
            const cf = e.skeleton
                ? deathSeg.start
                : dieSeg.start + dir * (dieSeg.frame + dieSeg.skip) + (dieSeg.frame - 1);
            const offset = e.type === 'npc' ? A.getNpcOffset(e.appr) : A.getMonOffset(e.appr);
            const lib = e.type === 'npc' ? 'npc' : A.getMonImgLib(e.appr);
            return [{ lib, index: offset + cf }];
        }

        // attack/spell/struck/sitdown/digup/alive 一次性播放完回 stand；walk/run 循环播放（停止由 PlayScene 超时回 stand）
        // digup（SM_DIGUP 出土）：原版 AxeMon.pas:352 用 ActDeath 段正向播放
        // （ActDeath.start + dir*(frame+skip) 起，ftime 同 ActDeath），播完回 stand。
        // alive（SM_ALIVE 复活）：AxeMon.pas:424 同样播 ActDeath 段（按方向取段），
        // m_boDeath/m_boSkeleton := FALSE 后播完回 stand，即「躺尸抖一下站起来」。
        if (action === 'attack' || action === 'spell' || action === 'struck'
            || action === 'sitdown' || action === 'digup' || action === 'alive') {
            const seg = segs[segKey] || segs.stand;
            const af = A.actionFrames(0, seg, dir);
            if (af.ftime > 0 && (now - at) > af.count * af.ftime) {
                segKey = 'stand';
            }
        }

        const seg = segs[segKey] || segs.stand;
        const af = A.actionFrames(0, seg, dir);   // offset 统一为 0，怪物/NPC 之后再加外观 offset

        // 相对帧（0..count-1）
        let rel;
        // 原版 SM_DEATH（别人看到玩家死，Actor.pas:1040 `startframe := endframe`）
        // 直接定格 ActDie 末帧，完全不播动画；die（自己死 SM_NOWDEATH）才推进。
        if (e.type === 'player' && action === 'death') {
            rel = af.count - 1;
        } else if (action === 'die') {
            rel = af.ftime > 0 ? Math.min(Math.floor((now - at) / af.ftime), af.count - 1) : 0;
        } else if (segKey === 'stand') {
            rel = af.ftime > 0 ? Math.floor((now + this._phase) / af.ftime) % af.count : 0;
        } else {
            rel = af.ftime > 0 ? Math.floor((now - at) / af.ftime) % af.count : 0;
        }
        const cf = af.start + rel;   // 相对帧号（已含方向偏移）

        // 玩家分层：武器与衣服的前后关系随方向/动作帧变化（原版 Actor.pas THumActor.DrawChr + WORDER[Sex, currentframe]）
        //   wpord=0：武器先画（在衣服后面）→ 身体 → 头发
        //   wpord=1：身体 → 头发 → 武器后画（在衣服前面）
        // dress/weapon 由服务端按 Delphi GetRelFeature 算好（shape*2+sex；无装备 = sex）
        if (e.type === 'player') {
            const dress = e.dress != null ? e.dress : e.sex;
            const weapon = e.weapon != null ? e.weapon : 0;
            const sex = e.sex || 0;
            const layers = [];
            const wpord = cf >= 0 && cf < 600 && (A.WORDER[sex] || '')[cf] === '1';
            const weaponLayer = { lib: 'weapon', index: A.HUMANFRAME * weapon + cf };
            if (!wpord && weapon >= 2) {
                layers.push(weaponLayer);
            }
            layers.push({ lib: 'hum', index: A.HUMANFRAME * dress + cf });
            // 原版 Actor.pas:3183-3188：haircount=3，先把 hair 钳到 0..2 再 *2，
            // 只有 >1 才画（hair=0 → offset=-1 光头）。建角协议虽发 1..5
            // （IntroScn.pas:1427），但 3..5 在客户端一律按发型 2 画；
            // 不钳制会让 hair=3 算出 600*(6+sex)=4200 > Hair 库 3600 帧 → 光头。
            const hair = Math.min(Math.max(e.hair | 0, 0), A.HAIR_STYLES - 1) * 2;
            if (hair > 1) {
                layers.push({ lib: 'hair', index: A.HUMANFRAME * (hair + sex) + cf });
            }
            if (wpord && weapon >= 2) {
                layers.push(weaponLayer);
            }
            return layers;
        }

        // 怪物/NPC：单层，外观 offset + cf
        const offset = e.type === 'npc' ? A.getNpcOffset(e.appr) : A.getMonOffset(e.appr);
        const lib = e.type === 'npc' ? 'npc' : A.getMonImgLib(e.appr);
        const layers = [{ lib, index: offset + cf }];

        // 稻草人（RaceImg 18 → 客户端 THuSuABi）死亡动画期间在身体**之上**叠死亡火焰：
        //   AxeMon.pas:810 `EffectSurface := FrmMain.WMon3Img.GetCachedImage(
        //       DEATHFIREEFFECTBASE + m_nCurrentFrame - m_nStartFrame, ax, ay)`
        //   DrawChr 里 `DrawBlend(..., 1)` 半透明叠加（DEATHFIREEFFECTBASE = 2860，固定在 mon3）。
        // 原版 LoadSurface 有 60s 节流，会把火焰冻在某一帧直到 MakeGhost；这里逐帧播完即止。
        if (action === 'die' && e.raceImg === 18) {
            layers.push({ lib: 'mon3', index: 2860 + Math.min(rel, 9), blend: 'lighter' });
        }
        return layers;
    }

    // 取纹理（同步返回已加载，未加载触发异步并返回 null）。
    // 动画帧在本 WIL 里是连续编号，顺带预取后续 6 帧，避免每切换一帧才发一次
    // Range 请求、期间该层空白（人物时隐时现 = 肉眼可见的闪）。
    _texture(lib, index) {
        const key = lib + '/' + index;
        if (this._tex.has(key)) return this._tex.get(key);
        const wlib = getLib(lib);
        if (wlib && !this._loading.has(key)) {
            const load = (i, isCurrent) => {
                const k = lib + '/' + i;
                if (this._loading.has(k)) return;
                this._loading.add(k);
                this._tex.set(k, null);   // 占位，加载完成后覆盖
                wlib.getTexture(i).then(tex => {
                    this._tex.set(k, tex || null);
                    if (tex && isCurrent) this._lastByLib.set(lib, tex);
                }).catch(() => { /* 保留 null 占位，等待帧回退 */ });
            };
            load(index, true);
            for (let d = 1; d <= 6; d++) load(index + d, false);
        } else {
            this._tex.set(key, null);
        }
        return null;
    }
}
