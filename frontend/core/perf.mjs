// 帧内分阶段耗时统计（常驻，开销 = 每个阶段一次 performance.now）
//
// 为什么需要它：用户反馈「还是太卡」，但「卡」可能是网络（纹理没到）、
// 可能是主线程某一段重绘（地面层/对象层/实体/HUD），也可能是合成与 GC。
// 只靠肉眼与 Network 面板分不清。这里把每帧拆成 floor / obj / ent / build /
// map / fx / hud / minimap / update / blit 若干段，落进环形缓冲区，
// 由 F9 诊断导出（core/diag.mjs）与 F8 悬浮 HUD 读取。
//
// 约束：热路径上只做 4 次 performance.now() + 几个数字自增，不分配对象、
// 不拼接字符串（帧记录里的数字保持 raw，取统计时才取整）。

const CAP = 300;

class Perf {
    constructor() {
        /** 当前帧累加器（null 原型，避免原型链查找） */
        this.cur = Object.create(null);
        this.frames = [];
        this.n = 0;          // 累计帧数
        this.t0 = 0;         // 本帧起点
        this.tPrev = 0;      // 上一帧起点（算 rAF 间隔）
    }

    beginFrame() {
        const c = this.cur;
        for (const k in c) c[k] = 0;
        this.t0 = performance.now();
        c.t = this.t0;
        c.dt = this.tPrev ? this.t0 - this.tPrev : 0;
        this.tPrev = this.t0;
        this.cur = c;
    }

    /** 累加某阶段耗时（同帧多次调用会求和，例如 map 里逐行累加） */
    add(k, ms) {
        const c = this.cur;
        c[k] = (c[k] || 0) + ms;
    }

    /** 收尾：把本帧累加器写成一条记录（复用同一对象，减少 GC）。 */
    endFrame() {
        const c = this.cur;
        c.total = performance.now() - this.t0;
        const f = this.frames;
        if (f.length >= CAP) f.shift();
        f.push(c);
        // 下一帧用的是新对象，旧对象已进队列
        this.cur = Object.create(null);
        c.dt = c.dt || 0;
        this.n++;
        return c;
    }

    /** 最近 ms 时间窗内的帧记录（默认全部）。 */
    recent(ms = 0) {
        if (!ms) return this.frames;
        const since = performance.now() - ms;
        const f = this.frames;
        let i = f.length;
        while (i > 0 && f[i - 1].t >= since) i--;
        return f.slice(i);
    }

    /** 单键统计：p50/p95/p99/max/avg（raw 数值，取整由调用方决定）。 */
    stats(keys, frames = this.frames) {
        const out = {};
        if (!frames.length) return out;
        for (const k of keys) {
            const vals = new Array(frames.length);
            let sum = 0, max = 0;
            for (let i = 0; i < frames.length; i++) {
                const v = frames[i][k] || 0;
                vals[i] = v;
                sum += v;
                if (v > max) max = v;
            }
            vals.sort((a, b) => a - b);
            const at = (p) => vals[Math.min(vals.length - 1, Math.floor(vals.length * p))];
            out[k] = {
                p50: +at(0.5).toFixed(2), p95: +at(0.95).toFixed(2),
                p99: +at(0.99).toFixed(2), max: +max.toFixed(2),
                avg: +(sum / vals.length).toFixed(2),
            };
        }
        return out;
    }

    /** 掉帧计数：总帧耗时超过 budget 的帧数（默认 20ms ≈ 50fps 下限）。 */
    overBudget(budget = 20, frames = this.frames) {
        let n = 0, worst = 0;
        for (const f of frames) {
            const t = f.total || 0;
            if (t > budget) n++;
            if (t > worst) worst = t;
        }
        return { budget, frames: frames.length, over: n, worst: +worst.toFixed(2) };
    }

    reset() {
        this.frames.length = 0;
        this.n = 0;
        this.tPrev = 0;
        this.cur = Object.create(null);
    }
}

export const perf = new Perf();
export const PERF_KEYS = ['dt', 'total', 'update', 'render', 'floor', 'obj', 'ent',
                          'build', 'map', 'fx', 'hud', 'minimap', 'blit'];
