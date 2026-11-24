// wsUrl.mjs 解析规则单测：node scripts/test_ws_url.mjs（Node18+ 有全局 URL）
import { resolveWsUrl, DIRECT_WS_URL } from "../frontend/net/wsUrl.mjs";

let pass = 0, fail = 0;
function eq(href, want, label) {
    const got = resolveWsUrl(href);
    if (got === want) { pass++; console.log(`ok  ${label}: ${href} -> ${got}`); }
    else { fail++; console.log(`FAIL ${label}: ${href} -> ${got} (期望 ${want})`); }
}

// CF 隧道域名页 → 直连源站 IP
eq("https://mir2.notmmao.com/", DIRECT_WS_URL, "mir2 域名页直连");
eq("https://mir2.notmmao.com/?touch=1", DIRECT_WS_URL, "带其他参数仍直连");
eq("https://notmmao.com/", DIRECT_WS_URL, "裸域也直连");
// 非 notmmao 主机 → 同源
eq("http://127.0.0.1:8098/", "/ws", "本地回环同源");
eq("http://192.168.124.7:8123/", "/ws", "局域网同源");
eq("https://evil-notmmao.com/", "/ws", "仿冒后缀不误判");
eq("https://notmmao.com.evil.cn/", "/ws", "伪 notmmao.com 后缀不误判");
// ?ws 覆盖
eq(`https://mir2.notmmao.com/?ws=${encodeURIComponent("/ws")}`, "/ws", "?ws=/ws 强制同源");
eq("https://mir2.notmmao.com/?ws=same", "/ws", "?ws=same 强制同源");
eq("https://127.0.0.1:8098/?ws=wss%3A%2F%2F139.224.24.88%2Fws", DIRECT_WS_URL, "本地强制直连");
// 非法 href 兜底
eq("not a url", "/ws", "非法 URL 兜底同源");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
