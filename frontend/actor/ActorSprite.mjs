// 实体精灵：根据实体快照 + 动作状态（_action/_actionAt）计算当前帧图索引，并懒加载纹理。
// 对应 Delphi 客户端 TActor/THumActor 的帧计算（Actor.pas）。
// 渲染层按绘制顺序返回（武器→身体→头发，后者覆盖前者）。

import { getLib } from '../image/Images.mjs';
import * as A from './actorData.mjs';

// 动作名 → 动作表段名（玩家用 HA，怪物/NPC 用 MA）
// 施法（spell）映射到 HA.spell 段（start 392, 6 帧×60ms），与普通攻击 hit 段不同（原版 ActSpell）
// 冲撞（rush）映射到 run 段（原版 ActRushLeft/Right 即 run 段 128 起的 3 帧子段，见 Actor.pas）
const PLAYER_SEG = { stand: 'stand', walk: 'walk', run: 'run', rush: 'run', attack: 'hit', spell: 'spell', struck: 'struck', die: 'die', sitdown: 'sitdown' };
const MON_SEG = { stand: 'stand', walk: 'walk', attack: 'attack', struck: 'struck', die: 'die', death: 'death' };

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
        if (!e || e.type === 'player') return;
        const segs = e.type === 'npc' ? A.npcAction(e.appr) : A.raceByPM(e.raceImg);
        const seg = segs && (segs.death || segs.die);
        if (!seg) return;
        const cf = seg.start + (e.skeleton ? 0 : (e.dir || 0));
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
            const tex = this._texture(f.lib, f.index) || this._lastByLib.get(f.lib);
            if (tex) out.push({ canvas: tex.canvas, px: tex.px, py: tex.py });
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

        // 尸体静止帧：原版 Actor.pas::CalcActorFrame 对 SM_SKELETON 用
        // `ActDeath.start + m_btDir`，GetDefaultFrame 对 m_boDeath&&m_boSkeleton
        // 用 `ActDeath.start`（**不带方向**，骨头架子就一张）。
        // 这里按方向 +dir 偏移（**不**乘 frame+skip），与 die 段
        // （ActDie.start + dir*(frame+skip)）区分开。
        if (action === 'death' && e.type !== 'player') {
            const seg = segs.death || segs.die || segs.stand;
            const cf = seg.start + (e.skeleton ? 0 : dir);
            const offset = e.type === 'npc' ? A.getNpcOffset(e.appr) : A.getMonOffset(e.appr);
            const lib = e.type === 'npc' ? 'npc' : A.getMonImgLib(e.appr);
            return [{ lib, index: offset + cf }];
        }

        // attack/spell/struck/sitdown 一次性播放完回 stand；walk/run 循环播放（停止由 PlayScene 超时回 stand）
        if (action === 'attack' || action === 'spell' || action === 'struck'
            || action === 'sitdown') {
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
        if (action === 'die') {
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
            if ((e.hair || 0) >= 1) {
                layers.push({ lib: 'hair', index: A.HUMANFRAME * (e.hair * 2 + sex) + cf });
            }
            if (wpord && weapon >= 2) {
                layers.push(weaponLayer);
            }
            return layers;
        }

        // 怪物/NPC：单层，外观 offset + cf
        const offset = e.type === 'npc' ? A.getNpcOffset(e.appr) : A.getMonOffset(e.appr);
        const lib = e.type === 'npc' ? 'npc' : A.getMonImgLib(e.appr);
        return [{ lib, index: offset + cf }];
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
