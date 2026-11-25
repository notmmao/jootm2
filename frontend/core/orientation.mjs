// 移动端横屏辅助：触屏设备进入页面后，第一次点按（用户手势）即尝试
// 全屏 + 锁定横屏（Screen Orientation API）。
//
// 兼容性现实：
// - Android Chrome / Edge / WebView（含微信 X5）：requestFullscreen 需用户
//   手势触发；screen.orientation.lock('landscape') 仅在全屏下允许 → 两步一起做；
// - iOS Safari / 微信 iOS WKWebView：元素全屏与 orientation.lock 都不支持
//   （only-in-standalone-PWA），静默失败，由 index.html 的 #rotate-tip
//   横幅引导玩家手动旋转；
// - 失败不报错、可反复尝试：每次 pointerdown 若仍是竖屏就再试一轮，
//   直到锁定成功（orientation.type 变横向）或用户离开。
//
// 注意：这里只负责「尽量横屏」，缩放/布局仍由 viewport.fitStage 处理，
// 旋转事件天然触发 resize → fitStage 重排，无需额外工作。

import { isTouchDevice } from '../input/TouchControls.mjs';

const isPortrait = () => window.innerHeight > window.innerWidth;

export function installLandscapeAssist() {
    if (!isTouchDevice()) return;
    // 只在「小窗 + 竖屏」环境启用（手机）。触屏笔记本横屏宽窗直接跳过，
    // 绝不在桌面环境抢全屏。
    if (Math.min(window.innerWidth, window.innerHeight) > 560) return;
    if (!isPortrait()) return;
    // 只对有 orientation.lock 的环境启用（Android Chrome/Edge/WebView）。
    // iOS 不支持 lock——单独进全屏却锁不了方向反而更糟，直接跳过，
    // 由 index.html 的 #rotate-tip 横幅引导手动旋转。
    const so = screen && screen.orientation;
    if (!so || typeof so.lock !== 'function') return;

    let locked = false;
    let trying = false;

    const attempt = async () => {
        if (locked || trying) return;
        trying = true;
        try {
            const el = document.documentElement;
            if (!document.fullscreenElement && el.requestFullscreen) {
                // lock() 在 Chrome 上要求先进全屏；requestFullscreen 需用户手势，
                // 本函数正是从 pointerdown 里调用的。
                await el.requestFullscreen({ navigationUI: 'hide' });
            }
            await so.lock('landscape');
            locked = true;
            document.removeEventListener('pointerdown', attempt, { capture: true });
        } catch (e) {
            // 失败静默：下次点按再试（部分 WebView 手势窗口过后才允许全屏）。
            console.debug('[orientation] 横屏锁定暂不可用', e && e.name);
        } finally {
            trying = false;
        }
    };

    // 首次点按即触发；未成功前每次点按重试，成功后移除监听。
    document.addEventListener('pointerdown', attempt, { capture: true });

    // 全屏中被系统旋回竖屏（lock 被浏览器解除）时，下次点按还能再锁。
    so.addEventListener('change', () => {
        if (locked && isPortrait()) { locked = false; document.addEventListener('pointerdown', attempt, { capture: true }); attempt(); }
    });
}
