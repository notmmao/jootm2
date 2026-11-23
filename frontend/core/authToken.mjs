// 免密登录 token 的 localStorage 键与登出清理（2026-11-23）。
// 独立小模块：LoginScene 与 SelectCharScene 互有引用，共用逻辑放这里避免循环 import。
// token 由服务端登录成功签发（30 天 HMAC，无状态）；用户名只记用户名、不存密码。

export const LS_TOKEN = 'jootm2.authToken';
export const LS_USER = 'jootm2.lastUsername';

/** 清除免密登录 token（保留用户名记忆）。选角界面「退出」= 真正登出时用。 */
export function clearAuthToken() {
    try { localStorage.removeItem(LS_TOKEN); } catch { /* 隐私模式 */ }
}
