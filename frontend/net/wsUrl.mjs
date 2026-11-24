// WebSocket 地址解析（2026-11-24 架构：页面/资源走 CF 隧道，WS 直连源站）
//
// 背景：https://mir2.notmmao.com 经 Cloudflare Tunnel，WS 也走隧道的话
// 回程绕 CF 洛杉矶出口，实测浏览器 RTT median 448~694ms（直连源站仅 11~15ms）。
// 而「未备案域名 + 阿里云 IP」的 TLS 会被 DPI 按 SNI 重置（含任意子域），
// 域名直连走不通；唯一免拦路径是 **IP 字面量**（浏览器对 IP 主机名不发 SNI），
// 配 Let's Encrypt 签给本 IP 的短效证书（TLS-ALPN-01 + shortlived profile，
// 6 天效期，服务器 cron 自动续），wss 即可受信直连。
//
// 规则：
//   1. ?ws=<url> 强制指定（调试用；?ws=same 强制回同源）
//   2. 页面在 *.notmmao.com（CF 隧道入口）→ 直连源站 IP
//   3. 其他（127.0.0.1 / localhost / 局域网 IP 开发）→ 同源 '/ws'
export const DIRECT_WS_URL = "wss://139.224.24.88/ws";

export function resolveWsUrl(href = (typeof location !== "undefined" ? location.href : "")) {
    let u;
    try { u = new URL(href); } catch { return "/ws"; }
    const forced = u.searchParams.get("ws");
    if (forced) return forced === "same" ? "/ws" : forced;
    if (u.hostname === "notmmao.com" || u.hostname.endsWith(".notmmao.com")) {
        return DIRECT_WS_URL;
    }
    return "/ws";
}
