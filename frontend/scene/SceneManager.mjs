// 场景管理器：持有当前场景，驱动生命周期，并把服务端消息路由给当前场景。

import { Scene } from './Scene.mjs';

export class SceneManager {
    constructor(conn, ctx, winManager) {
        this.conn = conn;
        this.ctx = ctx;
        this.winManager = winManager;
        this.current = null;
        // 「退出」回登录界面的实现由 main.mjs 注入：需要新建 WebSocket 并重新挂音频模块，
        // 场景层不掌握连接工厂，这样也避免 LoginScene ↔ SelectCharScene 循环依赖。
        this.onReturnLogin = null;

        this.bind(conn);
    }

    /** 把所有消息路由到当前场景（换过连接后要重新调用）。 */
    bind(conn) {
        this.conn = conn;
        conn.on('*', (msg) => {
            if (this.current) this.current.onMessage(msg.type, msg);
        });
        return conn;
    }

    switchTo(scene) {
        if (this.current) this.current.onExit();
        this.current = scene;
        if (scene) scene.onEnter();
    }

    update(ts) {
        if (this.current) this.current.update(ts);
    }

    render(ctx) {
        if (this.current) this.current.render(ctx);
    }
}
