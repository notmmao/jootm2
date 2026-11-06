"""游戏模板：与 Java 侧 Gson 序列化结果逐字段互通。

SQLite 的 ``std_items / monsters / magics`` 表里存的是 Gson JSON 文本；
本模块用 ``dataclass(slots=True)`` 复刻字段名，保证 Python 写回的模板
Java 能读、Java 写入的模板 Python 能读（这是双栈共用同一数据库的前提）。

CSV 列映射与 ``Database.mapStdItem / mapMonster / mapMagic`` 一致。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# 与 Database.num() 一致：空串或非数字一律当 0，绝不抛异常。
def num(value: str | int | None) -> int:
    if value is None:
        return 0
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _from_storage(cls, data: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


@dataclass(slots=True)
class ItemTemplate:
    """TStdItem。字段名必须和 Java ``ItemTemplate`` 一致（Gson 直接反射字段）。"""

    index: int = 0
    name: str = ""
    stdMode: int = 0
    shape: int = 0
    weight: int = 0
    aniCount: int = 0
    specialPwr: int = 0
    itemDesc: int = 0
    looks: int = 0
    duraMax: int = 0
    acMin: int = 0
    acMax: int = 0
    macMin: int = 0
    macMax: int = 0
    dcMin: int = 0
    dcMax: int = 0
    mcMin: int = 0
    mcMax: int = 0
    scMin: int = 0
    scMax: int = 0
    need: int = 0
    needLevel: int = 0
    price: int = 0
    stock: int = 0
    atkSpd: int = 0
    agility: int = 0
    accurate: int = 0
    mgAvoid: int = 0
    strong: int = 0
    undead: int = 0
    hpAdd: int = 0
    mpAdd: int = 0
    expAdd: int = 0
    effType1: int = 0
    effRate1: int = 0
    effValue1: int = 0
    effType2: int = 0
    effRate2: int = 0
    effValue2: int = 0

    @classmethod
    def from_storage(cls, data: dict) -> "ItemTemplate":
        return _from_storage(cls, data)

    def to_storage(self) -> dict:
        return asdict(self)

    def client_json(self) -> dict:
        """对应 Java ``ItemTemplate.toJson()``（下发给客户端的简化字段）。"""
        return {
            "name": self.name, "stdMode": self.stdMode, "shape": self.shape,
            "weight": self.weight, "looks": self.looks, "duraMax": self.duraMax,
            "dc": self.dcMin, "mc": self.mcMin, "sc": self.scMin,
            "ac": self.acMin, "mac": self.macMin,
            "needLevel": self.needLevel, "price": self.price,
        }


@dataclass(slots=True)
class MonsterTemplate:
    name: str = ""
    race: int = 0
    raceImg: int = 0
    appr: int = 0
    level: int = 0
    undead: int = 0
    exp: int = 0
    hp: int = 0
    mp: int = 0
    ac: int = 0
    mac: int = 0
    dc: int = 0
    maxDc: int = 0
    mc: int = 0
    sc: int = 0
    speed: int = 0
    hit: int = 0
    walkSpeed: int = 0
    attackSpeed: int = 0
    coolEye: int = 0

    @classmethod
    def from_storage(cls, data: dict) -> "MonsterTemplate":
        return _from_storage(cls, data)

    def to_storage(self) -> dict:
        return asdict(self)

    def client_json(self) -> dict:
        return {"name": self.name, "race": self.race, "appr": self.appr,
                "level": self.level, "hp": self.hp, "maxDc": self.maxDc}


@dataclass(slots=True)
class MagicTemplate:
    magId: int = 0
    name: str = ""
    attrs: list = field(default_factory=list)
    effectType: int = 0
    effect: int = 0
    spell: int = 0
    power: int = 0
    maxPower: int = 0
    defSpell: int = 0
    defPower: int = 0
    defMaxPower: int = 0
    job: int = 0
    needLevel: list = field(default_factory=lambda: [0, 0, 0])
    maxTrain: list = field(default_factory=lambda: [0, 0, 0])
    delay: int = 0

    def __post_init__(self) -> None:
        self.resolve()

    def resolve(self) -> None:
        """attrs 顺序见 Java ``MagicTemplate.resolve()``；Delay 字段 ×10。"""
        def at(i: int) -> int:
            if i < 0 or i >= len(self.attrs):
                return 0
            v = self.attrs[i]
            return num(v) if isinstance(v, (int, float, str)) else 0

        self.effectType, self.effect, self.spell = at(0), at(1), at(2)
        self.power, self.maxPower = at(3), at(4)
        self.defSpell, self.defPower, self.defMaxPower = at(5), at(6), at(7)
        self.job = at(8)
        self.needLevel = [at(9), at(11), at(13)]
        self.maxTrain = [at(10), at(12), at(14)]
        self.delay = at(15) * 10

    @classmethod
    def from_storage(cls, data: dict) -> "MagicTemplate":
        return _from_storage(cls, data)

    def to_storage(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- CSV 映射

def item_from_csv(row: list[str]) -> ItemTemplate:
    r = row + [""] * (24 - len(row))
    t = ItemTemplate()
    t.name = r[1]
    t.stdMode, t.shape, t.weight = num(r[2]), num(r[3]), num(r[4])
    t.aniCount, t.specialPwr, t.itemDesc = num(r[5]), num(r[6]), num(r[7])
    t.looks, t.duraMax = num(r[8]), num(r[9])
    t.acMin, t.acMax = num(r[10]), num(r[11])
    t.macMin, t.macMax = num(r[12]), num(r[13])
    t.dcMin, t.dcMax = num(r[14]), num(r[15])
    t.mcMin, t.mcMax = num(r[16]), num(r[17])
    t.scMin, t.scMax = num(r[18]), num(r[19])
    t.need, t.needLevel = num(r[20]), num(r[21])
    t.price, t.stock = num(r[22]), num(r[23])
    return t


def monster_from_csv(row: list[str]) -> MonsterTemplate:
    r = row + [""] * (22 - len(row))
    m = MonsterTemplate()
    m.name = r[0]
    m.race, m.raceImg, m.appr, m.level = num(r[1]), num(r[2]), num(r[3]), num(r[4])
    m.undead, m.coolEye, m.exp = num(r[5]), num(r[6]), num(r[7])
    m.hp, m.mp = num(r[8]), num(r[9])
    m.ac, m.mac, m.dc, m.maxDc = num(r[10]), num(r[11]), num(r[12]), num(r[13])
    m.mc, m.sc, m.speed, m.hit = num(r[14]), num(r[15]), num(r[16]), num(r[17])
    m.walkSpeed, m.attackSpeed = num(r[18]), num(r[21])
    return m


def magic_from_csv(row: list[str]) -> MagicTemplate:
    r = row + [""] * (19 - len(row))
    # Java: 除最后一列 Descr 之外全部按数字存入 attrs。
    attrs: list = [num(v) for v in r[2:-1]] + [r[-1]]
    return MagicTemplate(magId=num(r[0]), name=r[1], attrs=attrs)


def read_csv_rows(path: Path) -> list[list[str]]:
    """跳过表头与空行，语义与 Java ``importCsvTable`` 一致。"""
    import csv

    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return [r for r in rows[1:] if r and any(c.strip() for c in r)]


def dumps(obj) -> str:
    """与 Gson 默认输出等价：UTF-8 非转义中文、无多余空格。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
