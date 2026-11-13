#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""解析 Crystal/C# Legend of Mir 服务端的 Server.MirDB（BinaryWriter 顺序格式），
导出 ItemInfo / MonsterInfo / MagicInfo 三张 CSV，供 compare_mir2_db.py 使用。

    D:/Python310/python.exe scripts/crystal_mirdb_export.py <Server.MirDB> -o data/db_compare/<tag>

格式依据本地 Crystal 源码（D:/work/me/game/传奇/Crystal，Version=117）里各模型的
``Load(BinaryReader)`` 按 ``version=105`` 的门取用（mir2ServerRelease 的
Server.MirDB 实测版本头 105）：
Envir.LoadDB 顺序 = header(10×int32) → MapInfo[] → ItemInfo[] → MonsterInfo[]
→ NPCInfo[] → QuestInfo[] → DragonInfo → MagicInfo[] → GameShopItem[]
→ ConquestInfo[] → RespawnTimer。
**整文件必须恰好消费到 EOF**——这是解析正确性的唯一硬验证，失败即报错。
"""

import argparse
import csv
import struct
import sys
from pathlib import Path

VERSION = 105  # mir2ServerRelease/Server.MirDB 的版本头


class Reader:
    """C# BinaryWriter 的逆操作（小端，字符串 7-bit 变长长度 + UTF-8）。"""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise EOFError(f'need {n} bytes at {self.pos}, file size {len(self.data)}')
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    def i8(self) -> int:
        return self._take(1)[0]

    def boolean(self) -> bool:
        return self._take(1)[0] != 0

    def u16(self) -> int:
        return struct.unpack('<H', self._take(2))[0]

    def i16(self) -> int:
        return struct.unpack('<h', self._take(2))[0]

    def u32(self) -> int:
        return struct.unpack('<I', self._take(4))[0]

    def i32(self) -> int:
        return struct.unpack('<i', self._take(4))[0]

    def u64(self) -> int:
        return struct.unpack('<Q', self._take(8))[0]

    def i64(self) -> int:
        return struct.unpack('<q', self._take(8))[0]

    def f32(self) -> float:
        return struct.unpack('<f', self._take(4))[0]

    def f64(self) -> float:
        return struct.unpack('<d', self._take(8))[0]

    def string(self) -> str:
        # C# BinaryWriter.Write(string): 7-bit 编码变长长度 + UTF-8 字节
        length = 0
        shift = 0
        while True:
            b = self._take(1)[0]
            length |= (b & 0x7F) << shift
            if not (b & 0x80):
                return self._take(length).decode('utf-8', errors='replace')
            shift += 7

    def stats(self) -> dict:
        """Crystal Stats(BinaryReader)：count + (byte stat, int value) 对。"""
        return {r.i8(): r.i32() for _ in range(r.i32())}


r = None  # 模块级当前 Reader，简化下面的解析函数签名


def parse_mapinfo() -> dict:
    mp = {'Index': r.i32(), 'FileName': r.string(), 'Title': r.string()}
    mp['MiniMap'] = r.u16()
    r.i8()                        # Light
    mp['BigMap'] = r.u16()
    for _ in range(r.i32()):      # SafeZones
        r.i32(), r.i32()          # Location
        r.u16()                   # Size
        r.boolean()               # StartPoint
    for _ in range(r.i32()):      # Respawns
        r.i32()                   # MonsterIndex
        r.i32(), r.i32()          # Location
        r.u16()                   # Count
        r.u16()                   # Spread
        r.u16()                   # Delay
        r.i8()                    # Direction
        r.string()                # RoutePath
        r.u16()                   # RandomDelay   (version > 67)
        r.i32()                   # RespawnIndex
        r.boolean()               # SaveRespawnTime
        r.u16()                   # RespawnTicks
    for _ in range(r.i32()):      # Movements（实测每条 31 字节 = 22 + 9）
        r.i32()                   # MapIndex
        r.i32(), r.i32()          # Source
        r.i32(), r.i32()          # Destination
        r.boolean()               # NeedHole
        r.boolean()               # NeedMove
        r.i32()                   # ConquestIndex (version >= 69)
        r.boolean()               # ShowOnBigMap  (version >= 95)
        r.i32()                   # Icon
    r.boolean()                   # NoTeleport
    r.boolean()                   # NoReconnect
    r.string()                    # NoReconnectMap（早前布局漏了它——尾部藏着的字符串）
    for _ in range(10):           # NoRandom..NoNames, Fight (10 bool)
        r.boolean()
    r.boolean()                   # Fire
    r.i32()                       # FireDamage
    r.boolean()                   # Lightning
    r.i32()                       # LightningDamage
    r.i8()                        # MapDarkLight
    for _ in range(r.i32()):      # MineZones
        r.i32(), r.i32()          # Location
        r.u16()                   # Size
        r.i8()                    # Mine
    r.i8()                        # MineIndex
    r.boolean()                   # NoMount
    r.boolean()                   # NeedBridle
    r.boolean()                   # NoFight
    r.u16()                       # Music
    r.boolean()                   # NoTownTeleport     (version >= 78)
    r.boolean()                   # NoReincarnation    (version >= 79)
    # version >= 110: WeatherParticles(u16)；111: GT+GTIndex；114: 5 bool+2 int —— 105 均无
    return mp


def parse_iteminfo() -> dict:
    it = {
        'Idx': r.i32(), 'Name': r.string(),
        'Type': r.i8(), 'Grade': r.i8(), 'RequiredType': r.i8(),
        'RequiredClass': r.i8(), 'RequiredGender': r.i8(), 'Set': r.i8(),
        'Shape': r.i16(), 'Weight': r.i8(), 'Light': r.i8(),
        'RequiredAmount': r.i8(), 'Image': r.u16(), 'DuraMax': r.u16(),
        'StackSize': r.u16(), 'Price': r.u32(),
    }
    r.boolean()                   # StartItem
    r.i8()                        # Effect
    r.i8()                        # bools 打包（NeedIdentify 等）
    r.i16()                       # Bind
    r.i16()                       # Unique
    it['RandomStatsId'] = r.i8()
    r.boolean()                   # CanFastRun
    r.boolean()                   # CanAwakening
    it['Slots'] = r.i8()          # version > 83
    stats = r.stats()             # version > 84
    # Stat 枚举实测值（Shared/Data/Stat.cs）：MinAC=0 MaxAC=1 MinMAC=2 MaxMAC=3
    # MinDC=4 MaxDC=5 MinMC=6 MaxMC=7 MinSC=8 MaxSC=9 Accuracy=10 Agility=11
    # HP=12 MP=13 AttackSpeed=14 Luck=15
    it['AcMin'], it['AcMax'] = stats.get(0, 0), stats.get(1, 0)
    it['MacMin'], it['MacMax'] = stats.get(2, 0), stats.get(3, 0)
    it['DcMin'], it['DcMax'] = stats.get(4, 0), stats.get(5, 0)
    it['McMin'], it['McMax'] = stats.get(6, 0), stats.get(7, 0)
    it['ScMin'], it['ScMax'] = stats.get(8, 0), stats.get(9, 0)
    it['HP'], it['MP'] = stats.get(12, 0), stats.get(13, 0)
    it['AttackSpeed'] = stats.get(14, 0)
    it['Luck'] = stats.get(15, 0)
    if r.boolean():               # ToolTip
        r.string()
    return it


def parse_monsterinfo() -> dict:
    m = {
        'Idx': r.i32(), 'Name': r.string(),
        'Image': r.u16(), 'AI': r.i8(), 'Effect': r.i8(),
        'Level': r.u16(), 'ViewRange': r.i8(), 'CoolEye': r.i8(),
    }
    stats = r.stats()             # version > 84（枚举值见 parse_iteminfo 注释）
    m['HP'] = stats.get(12, 0)
    m['MinAC'], m['MaxAC'] = stats.get(0, 0), stats.get(1, 0)
    m['MinMAC'], m['MaxMAC'] = stats.get(2, 0), stats.get(3, 0)
    m['MinDC'], m['MaxDC'] = stats.get(4, 0), stats.get(5, 0)
    m['MinMC'], m['MaxMC'] = stats.get(6, 0), stats.get(7, 0)
    m['MinSC'], m['MaxSC'] = stats.get(8, 0), stats.get(9, 0)
    m['Accuracy'], m['Agility'] = stats.get(10, 0), stats.get(11, 0)
    m['Light'] = r.i8()
    m['AttackSpeed'] = r.u16()
    m['MoveSpeed'] = r.u16()
    m['Experience'] = r.u32()
    m['CanPush'] = r.boolean()
    m['CanTame'] = r.boolean()
    m['AutoRev'] = r.boolean()
    m['Undead'] = r.boolean()
    m['DropPath'] = r.string()    # version > 89
    # version >= 115 CanRecall / 116 IsBoss —— 105 无
    return m


def parse_npcinfo() -> dict:
    npc = {'Index': r.i32(), 'MapIndex': r.i32()}
    for _ in range(r.i32()):      # CollectQuestIndexes
        r.i32()
    for _ in range(r.i32()):      # FinishQuestIndexes
        r.i32()
    npc['FileName'] = r.string()
    npc['Name'] = r.string()
    npc['X'], npc['Y'] = r.i32(), r.i32()
    npc['Image'] = r.u16()        # (version >= 72)
    npc['Rate'] = r.u16()
    npc['TimeVisible'] = r.boolean()   # (version >= 64)
    npc['HourStart'] = r.i8()
    npc['MinuteStart'] = r.i8()
    npc['HourEnd'] = r.i8()
    npc['MinuteEnd'] = r.i8()
    npc['MinLev'] = r.i16()
    npc['MaxLev'] = r.i16()
    npc['DayofWeek'] = r.string()
    npc['ClassRequired'] = r.string()
    r.i32()                       # Conquest     (version >= 66)
    r.i32()                       # FlagNeeded
    r.boolean()                   # ShowOnBigMap (version > 95)
    r.i32()                       # BigMapIcon
    r.boolean()                   # CanTeleportTo (version > 96)
    # ConquestVisible 仅 version >= 107 —— 105 无
    return npc


def parse_questinfo():
    r.i32()                       # Index
    r.string()                    # Name
    r.string()                    # Group
    r.string()                    # FileName
    r.i32()                       # RequiredMinLevel
    r.i32()                       # RequiredMaxLevel
    r.i32()                       # RequiredQuest
    r.i8()                        # RequiredClass
    r.i8()                        # Type
    r.string()                    # GotoMessage
    r.string()                    # KillMessage
    r.string()                    # ItemMessage
    r.string()                    # FlagMessage
    r.i32()                       # TimeLimitInSeconds (version > 90)


def parse_magicinfo() -> dict:
    return {
        'MagName': r.string(), 'Spell': r.i8(),
        'BaseCost': r.i8(), 'LevelCost': r.i8(), 'Icon': r.i8(),
        'Level1': r.i8(), 'Level2': r.i8(), 'Level3': r.i8(),
        'Need1': r.u16(), 'Need2': r.u16(), 'Need3': r.u16(),
        'DelayBase': r.u32(), 'DelayReduction': r.u32(),
        'PowerBase': r.u16(), 'PowerBonus': r.u16(),
        'MPowerBase': r.u16(), 'MPowerBonus': r.u16(),
        'Range': r.i8(),              # version > 66
        'MultiplierBase': r.f32(),    # version > 70
        'MultiplierBonus': r.f32(),
    }


def parse_gameshop():
    r.i32()                       # ItemIndex
    r.i32()                       # GIndex
    r.u32()                       # GoldPrice
    r.u32()                       # CreditPrice
    r.u16()                       # Count (version > 84)
    r.string()                    # Class
    r.string()                    # Category
    r.i32()                       # Stock
    r.boolean()                   # iStock
    r.boolean()                   # Deal
    r.boolean()                   # TopItem
    r.i64()                       # Date.ToBinary()
    # CanBuyCredit/CanBuyGold 仅 version > 105


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Crystal Server.MirDB -> CSV')
    ap.add_argument('mirdb', help='Server.MirDB 路径')
    ap.add_argument('-o', '--out', required=True, help='输出目录')
    args = ap.parse_args(argv)

    global r
    r = Reader(Path(args.mirdb).read_bytes())

    version = r.i32()
    custom_version = r.i32()
    for _ in range(8):            # Map/Item/Monster/NPC/Quest/Gameshop/Conquest/Respawn Index
        r.i32()

    map_count = r.i32()
    maps = [parse_mapinfo() for _ in range(map_count)]

    item_count = r.i32()
    items = [parse_iteminfo() for _ in range(item_count)]

    monster_count = r.i32()
    monsters = [parse_monsterinfo() for _ in range(monster_count)]

    npc_count = r.i32()
    npcs = [parse_npcinfo() for _ in range(npc_count)]

    quest_count = r.i32()
    for _ in range(quest_count):
        parse_questinfo()

    r.boolean()                   # DragonInfo.Enabled
    r.string()                    # MapFileName
    r.string()                    # MonsterName
    r.string()                    # BodyName
    for _ in range(6):            # Location/DropAreaTop/DropAreaBottom
        r.i32()
    for _ in range(12):           # Exps: long[MaxDragonLevel-1]
        r.i64()

    magic_count = r.i32()
    magics = [parse_magicinfo() for _ in range(magic_count)]

    gameshop_count = r.i32()
    for _ in range(gameshop_count):
        parse_gameshop()

    conquest_count = r.i32()
    if conquest_count:
        raise RuntimeError(f'ConquestInfos 有 {conquest_count} 条，解析器未实现该节')

    r.i8()                        # RespawnTimer.BaseSpawnRate
    r.u64()                       # CurrentTickcounter
    for _ in range(r.i32()):      # RespawnTickOption[]
        r.i32()                   # UserCount
        r.f64()                   # DelayLoss

    if r.pos != len(r.data):
        raise RuntimeError(
            f'文件未消费完：pos={r.pos} / {len(r.data)}，'
            f'解析器与存档版本（{version}）不匹配，禁止采信导出结果')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # NPC 的 MapIndex 换成地图 FileName（与本地 MerChant.txt 第 2 列同空间）
    map_name = {mp['Index']: mp['FileName'] for mp in maps}
    for npc in npcs:
        npc['MapFileName'] = map_name.get(npc['MapIndex'], '')
    tables = [('StdItems', items), ('Monster', monsters), ('Magic', magics),
              ('Npcs', npcs), ('Maps', maps)]
    for name, rows in tables:
        header = list(rows[0].keys())
        with open(out / f'{name}.csv', 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows([[row[k] for k in header] for row in rows])
        print(f'[{name}] {len(rows)} rows -> {out / (name + ".csv")}')
    print(f'[meta] version={version} custom={custom_version} '
          f'maps={map_count} items={item_count} monsters={monster_count} '
          f'npcs={npc_count} quests={quest_count} magics={magic_count} '
          f'gameshop={gameshop_count} conquest={conquest_count} '
          f'| EOF 精确消费 OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
