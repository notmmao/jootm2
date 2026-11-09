// 音频链路的零依赖前端回归（Node 22+，不需要浏览器）：
//   node scripts/test_audio.mjs
// 用最小 DOM/Audio/localStorage/fetch 桩驱动 SoundManager，覆盖索引、编号播放、
// 节流、404→WAV 回退、BGM、事件映射、按钮/背包点击、开关与持久化。

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const map = JSON.parse(readFileSync(path.join(root, 'frontend/audio/audio_map.json'), 'utf8'));

const log = [];
class FakeAudio {
    constructor(src = '') {
        this.src = src;
        this.paused = true;
        this.ended = false;
        this.volume = 1;
        this._listeners = new Map();
        FakeAudio.created.push(this);
    }
    addEventListener(type, fn) {
        if (!this._listeners.has(type)) this._listeners.set(type, []);
        this._listeners.get(type).push(fn);
    }
    removeEventListener(type, fn) {
        const l = this._listeners.get(type) || [];
        const i = l.indexOf(fn);
        if (i >= 0) l.splice(i, 1);
    }
    fire(type) { for (const fn of (this._listeners.get(type) || []).slice()) fn(); }
    cloneNode() { return new FakeAudio(this.src); }
    play() { this.paused = false; FakeAudio.played.push(this.src); return Promise.resolve(); }
    pause() { this.paused = true; }
}
FakeAudio.created = [];
FakeAudio.played = [];

const storage = new Map();
class FakeElement {
    constructor(props) { Object.assign(this, props); }
}
globalThis.Audio = FakeAudio;
globalThis.Element = FakeElement;
globalThis.HTMLInputElement = class {};
globalThis.HTMLTextAreaElement = class {};
globalThis.document = { addEventListener() {} };
globalThis.localStorage = {
    getItem: (k) => storage.has(k) ? storage.get(k) : null,
    setItem: (k, v) => storage.set(k, String(v)),
};
globalThis.fetch = async (url) => {
    assert.equal(url, '/audio/audio_map.json');
    return { ok: true, json: async () => map };
};
globalThis.btoa = globalThis.btoa || ((s) => Buffer.from(s, 'binary').toString('base64'));

const { sound, SFX, itemClickSound } = await import('../frontend/audio/SoundManager.mjs');

let passed = 0, total = 0;
function ck(label, fn) {
    total++;
    try { fn(); passed++; console.log('  PASS ' + label); }
    catch (e) { console.error('  FAIL ' + label + ' :: ' + e.message); process.exitCode = 1; }
}
const lastUrl = () => FakeAudio.played[FakeAudio.played.length - 1];
const playCount = () => FakeAudio.played.length;

ck('索引规模：714 编号 / 723 mp3 / 7 BGM / 15 缺失', () => {
    assert.equal(Object.keys(map.sounds).length, 714);
    assert.equal(Object.keys(map.files).length, 723);
    assert.equal(Object.keys(map.bgm).length, 7);
    assert.equal(map.missing.length, 15);
    assert.equal(map.audioDir, 'Audio/');
});
ck('SoundUtil.ItemClickSound 映射', () => {
    assert.equal(itemClickSound(0), SFX.clickDrug);
    assert.equal(itemClickSound(5), SFX.clickWeapon);
    assert.equal(itemClickSound(22), SFX.clickRing);
    assert.equal(itemClickSound(999), SFX.itemClick);
});
ck('132 号指向非数字文件名 M12-1.wav', () => {
    assert.equal(map.sounds['132'].wav, 'Wav/M12-1.wav');
});
ck('BGM 文件名大小写/空格按磁盘实际解析', () => {
    assert.equal(map.bgm.gameover.wav, 'Wav/Game over2.wav');
    assert.equal(map.bgm.field.wav, 'Wav/Field2.wav');
});

const handlers = [];
const conn = { on: (t, h) => handlers.push([t, h]), onClose: null };
sound.install(conn);
await sound._loadMap();
sound._unlock();

ck('install 注册 * 通配监听并加载索引', () => {
    assert.ok(handlers.some(([t]) => t === '*'));
    assert.equal(sound.status().soundIds, 714);
    assert.equal(sound.status().unlocked, true);
});
ck('按编号播放 mp3（/resources/Audio/103.mp3）', () => {
    sound.lastAt.clear();
    assert.equal(sound.play(103), true);
    assert.equal(lastUrl(), '/resources/Audio/103.mp3');
});
ck('同类事件 80ms 节流', () => {
    assert.equal(sound.play(103), false);
});
ck('sound.lst 缺失编号静默跳过', () => {
    assert.equal(sound.play(291), false);
});
ck('mp3 404 → 回退原始 WAV（只回退一次）', () => {
    sound.sounds['104'] = { mp3: 'missing-on-purpose.mp3', wav: 'Wav/104.wav' };
    sound.lastAt.clear();
    assert.equal(sound.play(104), true);
    const el = FakeAudio.created[FakeAudio.created.length - 1];
    el.fire('error');
    assert.equal(lastUrl(), '/resources/Wav/104.wav');
    const n = playCount();
    el.fire('error');
    assert.equal(playCount(), n);
});
ck('BGM 场景切换与音量', () => {
    sound.setBgm('field');
    assert.ok(lastUrl().endsWith('/Audio/' + map.bgm.field.mp3));
    sound.setBgm('select');
    assert.ok(lastUrl().endsWith('/Audio/' + map.bgm.select.mp3));
    assert.equal(sound.setBgmVolume(0.3), 0.3);
    assert.equal(sound.bgmAudio.volume, 0.3);
});
ck('开关与音量持久化到 localStorage', () => {
    assert.equal(sound.toggle(), false);
    assert.equal(storage.get('mir2.sound.enabled'), '0');
    assert.equal(sound.toggle(), true);
    assert.equal(storage.get('mir2.sound.enabled'), '1');
    sound.setVolume(0.5);
    assert.equal(storage.get('mir2.sound.volume'), '0.5');
});
ck('enterGame → selfId + field BGM', () => {
    sound._onMessage({ type: 'enterGame', player: { id: 7 } });
    assert.equal(sound.selfId, 7);
    assert.equal(sound.bgmKey, 'field');
});
ck('move(自己) → 脚步声；节流 250ms', () => {
    sound.lastAt.clear();
    FakeAudio.played.length = 0;
    sound._onMessage({ type: 'move', id: 7, run: false });
    assert.equal(lastUrl(), '/resources/Audio/1.mp3');
    sound._onMessage({ type: 'move', id: 7, run: false });
    assert.equal(playCount(), 1);
});
ck('attack(自己, 烈火 swordHit=26) → 137；被打 → 138', () => {
    sound.lastAt.clear();
    FakeAudio.played.length = 0;
    sound._onMessage({ type: 'attack', attackerId: 7, targetId: 9, swordHit: 26, damage: 12 });
    assert.ok(lastUrl().endsWith('/137.mp3'));
    sound._onMessage({ type: 'attack', attackerId: 9, targetId: 7, damage: 5 });
    assert.ok(lastUrl().endsWith('/138.mp3'));
});
ck('金币增加 → 106', () => {
    sound.lastAt.clear();
    FakeAudio.played.length = 0;
    sound._onMessage({ type: 'bagUpdate', gold: 100 });
    sound._onMessage({ type: 'bagUpdate', gold: 140 });
    assert.ok(lastUrl().endsWith('/106.mp3'));
});
ck('spellCast → 技能音；death(自己) → 144；itemDrop(金币) → 106', () => {
    sound.lastAt.clear();
    FakeAudio.played.length = 0;
    sound._onMessage({ type: 'spellCast', casterId: 7, magicId: 1 });
    assert.ok(lastUrl().endsWith('/137.mp3'));
    sound._onMessage({ type: 'death', id: 7 });
    assert.ok(lastUrl().endsWith('/144.mp3'));
    sound._onMessage({ type: 'itemDrop', id: 1, name: '金币' });
    assert.ok(lastUrl().endsWith('/106.mp3'));
});
ck('全局点击：普通按钮 103 / data-sfx=108 药品格', () => {
    sound.lastAt.clear();
    FakeAudio.played.length = 0;
    sound._onClick({ target: new FakeElement({
        closest: () => null, style: { cursor: 'pointer' }, parentElement: null }) });
    assert.ok(lastUrl().endsWith('/103.mp3'));
    sound.lastAt.clear();
    sound._onClick({ target: new FakeElement({
        closest: () => ({ dataset: { sfx: '108' } }), parentElement: null }) });
    assert.ok(lastUrl().endsWith('/108.mp3'));
});
ck('status() 调试字段齐全', () => {
    const s = sound.status();
    for (const k of ['enabled', 'unlocked', 'mapReady', 'fallback', 'bgmKey',
                     'soundIds', 'missingIds', 'volume', 'bgmVolume', 'selfId']) {
        assert.ok(k in s, k);
    }
});

console.log(`音频前端回归: ${passed}/${total} PASS`);
if (process.exitCode) process.exit(1);
