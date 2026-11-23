// 移动端音频回归（平板托管后无声）：node scripts/test_audio_mobile.mjs
// 用最小 DOM/Audio/timer 桩验证两个修复点：
//   1) 解锁监听常驻（不再 once）——NotAllowedError 置 unlocked=false 后，
//      下一次手势能重新解锁并续播 BGM；
//   2) 音效槽 voices 在 ended 不触发（iOS 切后台）时有超时回收，
//      不会被撑满导致后续音效永久静默。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const map = JSON.parse(readFileSync(path.join(root, 'frontend/audio/audio_map.json'), 'utf8'));

let passed = 0, total = 0;
async function ck(label, fn) {
    total++;
    try { await fn(); passed++; console.log('  PASS ' + label); }
    catch (e) { console.error('  FAIL ' + label + ' :: ' + e.message); process.exitCode = 1; }
}

// ---- 桩 ----
const listeners = new Map();          // type -> [{fn, opts}]
const timers = new Map();             // id -> {fn, ms}
let timerId = 0;
globalThis.setTimeout = (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); };
function fireTimers(pred = () => true) {
    for (const [id, { fn, ms }] of [...timers.entries()]) {
        if (pred(ms)) { timers.delete(id); fn(); }
    }
}

class FakeAudio {
    constructor(src = '') {
        this.src = src; this.paused = true; this.ended = false;
        this.volume = 1; this.muted = false; this._l = new Map();
        FakeAudio.created.push(this);
    }
    addEventListener(t, fn) {
        if (!this._l.has(t)) this._l.set(t, []);
        this._l.get(t).push(fn);
    }
    removeEventListener(t, fn) {
        const l = this._l.get(t) || [];
        const i = l.indexOf(fn);
        if (i >= 0) l.splice(i, 1);
    }
    fire(t) { for (const fn of (this._l.get(t) || []).slice()) fn(); }
    cloneNode() { return new FakeAudio(this.src); }
    play() {
        this.paused = false;
        if (FakeAudio.rejectMode === 'NotAllowedError') {
            const e = new Error('blocked');
            e.name = 'NotAllowedError';
            return Promise.reject(e);
        }
        FakeAudio.played.push(this.src);
        return Promise.resolve();
    }
    pause() { this.paused = true; }
}
FakeAudio.created = [];
FakeAudio.played = [];
FakeAudio.rejectMode = null;

class FakeElement { constructor(p) { Object.assign(this, p); } }
globalThis.Audio = FakeAudio;
globalThis.Element = FakeElement;
globalThis.HTMLInputElement = class {};
globalThis.HTMLTextAreaElement = class {};
globalThis.document = {
    hidden: false,
    addEventListener(type, fn, opts) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push({ fn, opts: opts || {} });
    },
};
const storage = new Map();
globalThis.localStorage = {
    getItem: (k) => storage.has(k) ? storage.get(k) : null,
    setItem: (k, v) => storage.set(k, String(v)),
};
globalThis.fetch = async () => ({ ok: true, json: async () => map });
globalThis.btoa = globalThis.btoa || ((s) => Buffer.from(s, 'binary').toString('base64'));
const fireDom = (type) => { for (const { fn } of (listeners.get(type) || []).slice()) fn(); };
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };

const { sound } = await import('../frontend/audio/SoundManager.mjs');
const conn = { on() {}, onClose: null };
sound.install(conn);
await sound._loadMap();
sound._unlock();

await ck('pointerdown/keydown/touchstart 解锁监听常驻（无 once）', () => {
    for (const t of ['pointerdown', 'keydown', 'touchstart']) {
        const ls = listeners.get(t) || [];
        assert.ok(ls.length >= 1, `缺少 ${t} 监听`);
        assert.notEqual(ls[0].opts.once, true, `${t} 不应设 once`);
    }
});

await ck('注册 visibilitychange 回前台续播 BGM', () => {
    assert.ok((listeners.get('visibilitychange') || []).length >= 1);
});

await ck('NotAllowedError → unlocked=false；下次手势重新解锁并续播 BGM', async () => {
    FakeAudio.rejectMode = 'NotAllowedError';
    sound.lastAt.clear();
    sound.play(103);
    await flush();
    assert.equal(sound.unlocked, false, '被拒后应标记未解锁');
    FakeAudio.rejectMode = null;
    const before = FakeAudio.played.length;
    fireDom('pointerdown');
    assert.equal(sound.unlocked, true, '下一次手势应重新解锁');
    assert.ok(FakeAudio.played.length > before, '应借手势续播 BGM');
});

await ck('已解锁状态下每次手势也尝试续播被暂停的 BGM', () => {
    sound.unlocked = true;
    sound.bgmAudio.pause();
    assert.equal(sound.bgmAudio.paused, true);
    fireDom('pointerdown');
    assert.equal(sound.bgmAudio.paused, false, '手势后 BGM 应恢复播放');
});

await ck('voices 在 ended 不触发时有超时回收，不会永久撑满', () => {
    sound.unlocked = true;
    sound.voices.clear();
    const createdBefore = FakeAudio.created.length;
    for (let i = 0; i < sound.maxVoices + 3; i++) {
        sound.lastAt.clear();
        sound.play(103);
    }
    // 同名并发会 clone；每个非 bgm 元素都注册了 15s reaper
    const reapers = [...timers.values()].filter((t) => t.ms === 15000);
    assert.ok(reapers.length > 0, '应有 voices 超时回收定时器');
    fireTimers((ms) => ms === 15000);
    assert.equal(sound.voices.size, 0, '超时后 voices 应清空');
    sound.lastAt.clear();
    sound.play(103);
    assert.ok(FakeAudio.created.length > createdBefore, '回收后应能继续播新音效');
});

await ck('cleanup 幂等：重复触发 ended 不会误删其它 voice', () => {
    sound.voices.clear();
    sound.lastAt.clear();
    sound.play(103);
    const el = [...sound.voices][0];
    assert.ok(el);
    el.fire('ended');
    el.fire('ended');
    assert.equal(sound.voices.size, 0);
});

console.log(`移动端音频回归: ${passed}/${total} PASS`);
if (process.exitCode) process.exit(1);
