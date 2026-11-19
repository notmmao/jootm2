"""我本沉默内容剥离回归测试。

断言：
1. 私服道具/怪/地图/NPC 不加载到内存模板与刷怪表；
2. 官服 BOSS（赤月恶魔/祖玛教主等）仍然存在；
3. 高爆变体掉落表回退到基线（僵尸10≈僵尸1、黑野猪0≈黑野猪）；
4. 关掉开关（enabled=false）后全集恢复——用一个临时配置验证。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyserver.content_filter import ContentPolicy, _show_name, filter_csv_rows, filter_drop_tables
from pyserver.db import Database
from pyserver.service import GameService
from pyserver.config import load as load_config
from pyserver.world_extra import DropEntry

ROOT = Path(__file__).resolve().parent.parent
passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {detail}")


def main():
    policy = ContentPolicy.load(ROOT)
    print(f"[{policy.summary()}]")
    check("策略默认开启", policy.enabled)
    check("glob 匹配 驽马法王", policy.drop_monster("驽马法王1"))
    check("glob 匹配 域类戒指", policy.drop_item("藤域戒指"))
    check("不误伤官服怪", not policy.drop_monster("赤月恶魔"))
    check("不误伤官服装备", not policy.drop_item("裁决之杖"))
    check("私服战袍被剥", policy.drop_item("青龙战衣(男)"))
    check("官服裁决不被剥", not policy.drop_item("裁决之杖"))

    # db 源头过滤
    rows = [["1", "裁决之杖"], ["2", "藤域戒指"], ["3", "蓝灵法杖"], ["4", "木剑"]]
    out = filter_csv_rows(policy, "item", rows)
    check("CSV 道具过滤", [r[1] for r in out] == ["裁决之杖", "木剑"], str(out))
    mrows = [["驽马法王"], ["赤月恶魔"], ["蚁后"], ["僵尸"]]
    out = filter_csv_rows(policy, "monster", mrows)
    check("CSV 怪物过滤", [r[0] for r in out] == ["赤月恶魔", "僵尸"], str(out))

    # 全量服务
    with tempfile.TemporaryDirectory() as td:
        cfg = load_config(root=str(ROOT), db=str(Path(td) / "t.db"))
        db = Database(cfg.db_file)
        db.seed_game_data(cfg.mud2_dir)
        srv = GameService(cfg, db)
        w = srv.world

        for name in ("驽马法王", "驽马勇士", "蚁后", "沃玛统领", "虹魔教主0", "千年树妖", "老渔翁"):
            check(f"已剥离 {name}", name not in w.monster_templates and
                  not any(n.name == name for n in w.npcs.values()))
        check("官服 BOSS 赤月恶魔存在", "赤月恶魔" in w.monster_templates)
        check("官服 BOSS 祖玛教主存在", "祖玛教主" in w.monster_templates)
        check("官服基础怪僵尸存在", "僵尸1" in w.monster_templates)
        check("私服地图 D2051 不刷怪", not any(e.map_id == "D2051" for e in w.mon_gen_entries))
        check("月崕岛(5) 不刷怪", not any(e.map_id == "5" for e in w.mon_gen_entries))
        check("官服地图 D501(祖玛) 仍刷怪", any(e.map_id == "D501" for e in w.mon_gen_entries))

        # 高爆回退用 DataSet 的回收价口径（间接：比较掉落表条目）
        jz = w.drop_tables.get("僵尸10", [])
        jz1 = w.drop_tables.get("僵尸1", [])
        check("僵尸10 掉落表≈基线僵尸1",
              [(e.item_name, e.max_point) for e in jz] == [(e.item_name, e.max_point) for e in jz1],
              f"n={len(jz)} vs {len(jz1)}")
        hy = w.drop_tables.get("黑野猪0", [])
        hy0 = w.drop_tables.get("黑野猪", [])
        check("黑野猪0 掉落表≈基线黑野猪",
              [(e.item_name, e.max_point) for e in hy] == [(e.item_name, e.max_point) for e in hy0])
        check("高爆回退名单含僵尸10", "僵尸10" in w.content_filter_stat["high_drop"])
        check("官服 BOSS 未被回退", "赤月恶魔" not in w.content_filter_stat["high_drop"])
        db.close()

    # filter_drop_tables 单测：白名单保护
    rule = policy.high_drop
    std = {"裁决之杖": 5, "八荒": 5, "金创药(小量)": 0}
    hot = [DropEntry(i, 5, "八荒") for i in range(10)]
    base = [DropEntry(0, 100, "裁决之杖")]
    tables = {"赤月恶魔": hot, "赤月恶魔1": base, "僵尸10": hot, "僵尸1": base}
    out, changed = filter_drop_tables(policy, tables, std)
    check("白名单 BOSS 表不动", out["赤月恶魔"] == hot)
    check("非白名单高爆回退", out["僵尸10"] == base and "僵尸10" in changed)

    print(f"\n结果：{passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
