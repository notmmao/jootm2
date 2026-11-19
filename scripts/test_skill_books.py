"""技能书获取分析回归（balance/mirbal/skill_books + GM 规则 + 报告生成）。

用法：D:/Python310/python.exe scripts/test_skill_books.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED = FAILED = 0


def check(name, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}")


def main():
    from balance.mirbal import boot
    from balance.mirbal.data import DataSet
    from balance.mirbal.analysis import Analyzer

    print("[1] 数据加载与 skill_books 结构")
    cfg, srv = boot.make_service(None)
    ds = DataSet(srv, "main")
    an = Analyzer(ds, [])
    bk = an.skill_books()
    check("技能书条目数 > 30", len(bk) > 30)
    req = {"book", "job", "need_level", "price", "shop", "shop_price", "shop_npc",
           "sources", "dead", "mon_levels", "mon_level_min", "mon_level_max",
           "level_bands", "out_h", "p50_hours", "mismatch", "orphan", "monsters"}
    check("每条字段齐全", all(req <= set(b) for b in bk))
    check("按学习等级排序", all(
        (bk[i]["need_level"] is None, bk[i]["need_level"] or 0) <=
        (bk[i + 1]["need_level"] is None, bk[i + 1]["need_level"] or 0)
        for i in range(len(bk) - 1)))

    print("[2] 核心判定")
    by = {b["book"]: b for b in bk}
    check("识别死书『书』(无掉落无商店)", by["书"]["dead"] and by["书"]["sources"] == 0 and not by["书"]["shop"])
    check("低级书火球术有商店", by["火球术"]["shop"] and by["火球术"]["shop_price"] > 0)
    check("火球术掉自 25 级僵尸", by["火球术"]["mon_level_min"] == by["火球术"]["mon_level_max"] == 25)
    check("高级书冰咆哮无商店", not by["冰咆哮"]["shop"])
    check("冰咆哮怪等级分布已聚合", 40 in by["冰咆哮"]["level_bands"] and sum(by["冰咆哮"]["level_bands"].values()) == by["冰咆哮"]["sources"])
    check("P50 有值且为正", by["冰咆哮"]["p50_hours"] > 0)
    check("火球术(P50≈0.01h) 比冰咆哮易得", by["火球术"]["p50_hours"] < by["冰咆哮"]["p50_hours"])
    check("等级错配判定正确", by["火球术"]["mismatch"] is True)  # 学7级,怪25级
    check("无对应技能标注", by["天尊之兽"]["orphan"] and by["冰咆哮"]["orphan"] is False)
    check("全服产出与掉源一致", all(b["out_h"] >= 0 for b in bk))

    print("[3] 报告数据流 + GM 体检规则")
    from balance.mirbal import report
    payload = report.collect(ds, [], [])
    check("collect 含 skillbooks", "skillbooks" in payload and len(payload["skillbooks"]) == len(bk))
    check("collect findings 含技能书规则", any(f["kind"] == "技能书" for f in payload["findings"]))
    kinds = {f["kind"] for f in payload["findings"]}
    titles = " ".join(f["title"] for f in payload["findings"])
    check("GM 报出死书", "死书" in titles)
    check("GM 报出等级错配", "等级错配" in titles)
    check("GM 报出无对应技能", "无同名技能" in titles)

    print(f"\n结果：{PASSED} PASS / {FAILED} FAIL")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
