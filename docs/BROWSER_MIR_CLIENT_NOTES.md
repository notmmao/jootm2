# 同类浏览器传奇（cn.stateofmir.com）前端技术观察

> 采集时间：2026-11-12。方式：抓 `https://cn.stateofmir.com/play/` 的 HTML 与全部 54 个
> `chunks/*.js`（合计 1.1MB），读代码 + 看响应头。**闭源**，以下是从产物反推的结论，
> 只取「对我们有用的做法」，不涉及任何代码复制。

## 1. 技术栈

| 项 | 观察 |
| --- | --- |
| 渲染 | **PixiJS v8**（`chunk-LXWYT5C4` = TextureSystem，含 `webGLVersion`、`texSubImage3D`、UBO、GPU 路径）；WebGL2 优先，降级 WebGL1 / GPU(GPUDevice) |
| 打包 | esbuild/rollup 风格：入口 `play.js` + 54 个 `chunk-<HASH>.js`，动态 `import()` 按需加载（手机端 `touch-*.js`、`phone-menu-*.js` 单独成块） |
| 素材 | **服务端预转换**，客户端完全不解析 WIL/WIX：`/play/data/converted/`（`maps/*.bin.gz`、`sound/`、`appearances.json` …）、`/play/data/content/`、`/play/data/ownmap/` |
| 地图 | 每张图一个 `maps/<id>.bin.gz`（0 号图 304KB），前端用 `DecompressionStream('gzip')` 解 |
| 状态同步 | WebSocket（`/play/game` 之类），版本号轮询 `version.json` → 「新版本 · 刷新」 |

## 2. 纹理：8 位索引 + 调色板 uniform

这是和我们差异最大、也最值得记的一点。它的纹理页有两种：

- `kind: "r16"` → `internalFormat = RGB565`（每像素 2 字节）；
- `kind: "i8"` → `internalFormat = R8`，`format = RED`（**每像素 1 字节索引**），
  配合 `uPalette`（`i32` 数组）在片元着色器里查表（`batch-r16` program + `r16Palette` uniform group）。

好处：显存/带宽降到 1/2~1/4，同一张贴图可以**瞬间换调色板**（中毒红/绿、昼夜染色、
隐身半透明、点击高亮都只是换 uniform），不需要像我们这样按颜色缓存染色后的 canvas
（`MapActor._tint`）。

上传也是分「行带」渐进式的：`texSubImage2D(..., rows, w, h, RED, UNSIGNED_BYTE, index)`
每次只推若干行（`u = floor(limit / w)`），避免一次性上传大页造成掉帧。

## 3. 缓存与刷新策略（可直接对照我们的实现）

1. **内容哈希 + 长缓存**：`/chunks/game-6JGV4MUN.js` 响应头
   `Cache-Control: public, max-age=31536000, immutable`；入口 `play.js` / `ui.css`
   是 `no-cache` + `ETag`（每次一个 304）。
2. **版本清单**：`/play/version.json` = `{"v":"636c561670ab8815"}`，页面轮询，
   发现变化提示刷新（避免玩家拿半新半旧的 chunk）。
3. **素材走 ETag 协商**（`maps/*.bin.gz` 为 `no-cache` + `ETag`），不是 immutable——
   因为素材是「预转换目录」而不是带哈希的文件名。
4. **重试**：`fetch` 失败按 `[500,1000,2000]ms` 退避重试；5xx/429 才重试。
5. **加载看门狗**：每一步 loading（`window.somLoad.step/idle/up`）都记录「当前步骤 +
   已停留秒数」，卡住超过 `data-stall-sec` 就弹「出错了 / 刷新」，并把
   `{v, step, stood, steps, up}` `sendBeacon` 到 `/play/err`。
   —— 这正是我们这次加 F9 诊断导出想解决的事：**现场要能带出去**。

## 4. 加载编排（对我们影响最大的一点）

`play.js` 里 `gameAhead()` 在**大厅/登录阶段就开始 `import()` 游戏主模块**，
`prepareGame(place, map)` 再按「即将进入的地图和出生点」预热——也就是
**先按地图粒度预取，再切场景**，切场景时素材已经在内存里。

我们原来的做法相反：进图后才 `collectVisibleTextures` 拉一遍，慢网下必然看到
「先暗屏、后补图」。这次改造后，我们把等价的语义做进了 `MapActor.enter` +
`mapPreload.cancelPreload()`：进图前掐掉后台预热、地面纹理插队、对象延迟到地面之后。

## 5. 其他细节

- `premultiply-alpha-on-upload` 探测：用 1×1 FBO `readPixels` 判断浏览器的预乘行为，
  按结果选着色器路径（省掉一遍 CPU 预乘）。
- CSP 严格（无内联脚本）：`gate.js` 是独立 classic script，先于 module 执行，
  老浏览器只显示一个盒子而不是白屏/报错。
- 移动端代码按需 `import()`，桌面端完全不下这部分字节。
- 服务端对素材路径做**白名单正则**（`maps/*.bin.gz`、`sound/`、`appearances/` …），
  防目录穿越；URL 分段 `encodeURIComponent`。

## 6. 我们采纳了什么 / 不采纳什么

已采纳（本次 `feat/python-server` 提交）：

- **素材按需精确区间**（替代 512KB 分片）——这是黑条/暗屏的根因，见
  `HANDOFF-PYSERVER.md` §31。
- **请求优先级 + 进游戏时 abort 在飞请求**（等价于他们「切场景前就把素材拉好」）。
- **加载看门狗思路** → 我们的 `Ctrl+F9` 现场导出（帧耗时分布 + 逐格状态 + 截图）。

暂不采纳（成本/收益不划算，或与原版像素级还原目标冲突）：

- **整体迁 PixiJS/WebGL**：我们大量依赖 2D canvas 的 `drawImage`/`filter`/
  `globalCompositeOperation` 语义来精确对齐 Delphi 的 `DrawBlend`、`ceBright`、
  `DrawEffSurface`；迁 GL 要重写全部绘制语义（几周量级），而实测**软件渲染下
  每帧也只有 1.4ms**（`--disable-gpu` 实测），说明瓶颈不在 2D 光栅化。
- **8 位索引纹理 + 调色板着色器**：同上，需要 shader 支持；我们的 `_tint`
  按（canvas, 颜色）缓存后开销已经可忽略。
- **服务端预转换素材**：`resources/` 是 1.80 客户端原始 WIL 的完整提取，
  改预转换等于重建整条素材管线，且会丢掉「按需只取一张图」的能力。
