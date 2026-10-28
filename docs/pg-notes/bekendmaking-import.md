# 官方公告(officiële bekendmakingen)入库:实测记录 2026-10-28

源:KOOP SRU,`https://repository.overheid.nl/sru`,CC0,库 `cup` 共 **7,404,221** 条。
迁移:`0005_bekendmaking.sql` + `0006`(几何类型)+ `0007`(唯一键)+ `0008`(有效性)。
脚本:`scripts/import_bekendmaking.py`。

这份笔记的重点不是"接上了",而是**四个只有真打才会暴露的约束**,其中两个是我自己写出来的 bug。

## 1. `locatiepunt` 不是地理字段,`locatiegebied` 才是

第一版按 `w.locatiepunt` 建模成 `geometry(Point,4326)`,理由是 SRU **只给这个字段建了空间索引**
(`w.locatiepunt within/rijksdriehoek`)。导入 854 条 Diemen 公告,写了 441 条位置,看起来像
"上游数据稀疏"。按类型拆开才露馅:`omgevingsvergunning` 只有 **14%** 有坐标。

拿一条无坐标的记录回源对照,真相是:

```
locatiepunt      — 源里也没有 —
geometrie        POLYGON ((126848.41 482722.17, ...))        RD New
locatiegebied    POLYGON((4.9742032 52.331713, ...))         WGS84
```

100 条 Diemen 2026 样本:

| 字段 | 出现 |
|---|---|
| `locatiepunt` | 46 / 100 |
| `locatiegebied` | **100 / 100** |

`locatiegebied` 一个字段里有**三种格式**,而且轴序不一致:

```
LINESTRING(4.957 52.327,...)   79   街段(交通决定)     WKT,lon lat
POLYGON((4.956 52.329,...))    53   地块(建设许可)     WKT,lon lat
52.338417,4.9600705            48   点                  lat,lon —— 反的
```

改用 `locatiegebied` 后,**每一个类型的覆盖率都是 100%**(原先 14–73%),位置行 441 → 1,637。

两条教训:
- **搜索索引建在哪个字段,不代表数据存在哪个字段。** 我把"能被空间检索的字段"当成了"地理字段"。
- **少收和"上游本来就少"长得一模一样。** 唯一能分辨的办法是拿样本回源对照,而不是看导入
  自己报的成功数。这和 ORS 那次"200 不是正确答案的证据"是同一类。

顺带:之前报的"全库 57% 带坐标"也是错的 —— 那是用 `w.locatiepunt within/...` 量的,
只数了点记录。真实的"有地理信息"比例更高,但无法用 SRU 自己的索引直接量。

## 2. btree 索引行上限 2704 字节:几何不能直接当唯一键

0005 用 `UNIQUE (identifier, geom)` 去重(`geometry` 确实有 btree opclass)。改用
`locatiegebied` 后第一次导入就炸:

```
index row size 6560 exceeds btree version 4 maximum 2704
HINT: Consider a function index of an MD5 hash of the value
```

一条街段 LINESTRING 几千字节,放不进 btree 条目(约 1/3 页)。**0005 能通过只是因为它在存
32 字节的 POINT —— 第一个 bug 替我挡住了第二个约束。**

按服务端自己的 HINT 改成 `UNIQUE (identifier, md5(ST_AsBinary(geom)))`。
代价:语义从"几何相等"收紧为"二进制序列化相等",顶点顺序或精度不同就算两条。
可接受,因为源在重复几何时是逐字重复的。

## 3. 深分页上限 10,000,而且 504 不 400

阿姆斯特丹 2026 有 53,719 条。导入死在:

```
startRecord=9801   http=200  返回 0 条      ← 静默空页
startRecord=9901   http=200  返回 0 条
startRecord=10000  http=504  Gateway Time-out
startRecord=10001  http=504
```

**9,900 以上先给空 200,10,000 起给 504。** 空的 200 比 504 更危险:一个只看
`if not recs: break` 的循环会当成"取完了"正常退出,`etl_run` 还会标成 succeeded。
我第一次探测上限时就被这个骗过,以为天花板是 10,000 返回空。

导入器因此先 probe `numberOfRecords`(`maximumRecords=0`),超过 9,900 就按月切,
月仍超就按天切。单日超上限的情况保留并明确打印,不静默丢。

`etl_run` 的状态机在这里按预期工作:那次失败留下
`status=failed` + 完整的 504 URL,没有假装成功。

## 4. 自相交多边形,PostGIS 只给 NOTICE

首批 495 个 POLYGON 里 **4 个无效**(Ring Self-intersection)。
`ST_Contains` 对无效多边形返回的是**未定义结果**,不是报错 —— 与
`stage1-poi-import.md` 里那个领结形环返回 28 行只带一条 NOTICE 完全同类。

`domains/geo/service.py` 在查询时修等时线,因为那个多边形是瞬时的。这些行是存下来的,
所以修在写入时:`CASE WHEN ST_IsValid(g) THEN g ELSE ST_CollectionExtract(ST_MakeValid(g),3) END`,
再加 `CHECK (ST_IsValid(geom))`,把"本表不可能存下会给出未定义答案的几何"变成**数据库强制的不变式**,
而不是导入器需要记得的约定。修完 4 个 POLYGON 变成 MULTIPOLYGON。

## 幂等性(实测)

`ON CONFLICT (identifier) DO UPDATE ... WHERE <整行 IS DISTINCT FROM>` +
位置表 `ON CONFLICT (identifier, md5(ST_AsBinary(geom))) DO NOTHING`。

```
重跑前 (ins/upd): bekendmaking=854/0   bekendmaking_locatie=2145/4
重跑后 (ins/upd): bekendmaking=854/0   bekendmaking_locatie=2145/4
```

**0 插入 0 更新。** 没有 `WHERE 行不同` 的话,重叠日期区间的重跑会重写每个元组、让堆翻倍 ——
stage 1 在 `poi` 上踩过同一个坑。

没有做 sentinel 检查(housing 的硬性要求之一),理由写在脚本 docstring 里:
`import_poi.py` 会软删除 extract 里不再出现的行,所以截断的下载会被记成大规模消失;
这个导入器**只 upsert,从不删**,所以短收只损失覆盖,不损害正确性。

## 核心查询跑通

以 Diemerplein 为心的 15 分钟骑行等时线(自建 ORS,32.74 km²)裁剪,按月 × 类型:

```
  月份   | 建设许可 | 活动许可 | 交通决定 | 经营许可 | 合计
 2026-06 |       41 |       15 |       30 |        5 |  198
 2026-09 |       42 |        4 |        4 |        8 |  142
 2026-10 |       26 |        2 |        5 |        5 |   94
```

注意:`JOIN bekendmaking_locatie` 会按几何扇出(一条公告多个几何),所以**每一个计数都必须
`count(DISTINCT identifier)`**。我第一版只给合计加了 DISTINCT,于是 2026-02 那行分项加起来
282、合计 119,自相矛盾。扇出型 join 上的裸 `count(*)` 是静默错误。

用 `w.activiteit`(9 个值的完整词表)分组更适合做 UI facet:

```
bouwen 276 | ruimtelijke ordening 60 | kappen 43 | milieu 36 | reclame 8 | slopen 6
```

## 为什么是 Postgres 而不是文档库

SRU 只能按**半径**查(`within/rijksdriehoek`,无多边形关系、无 `/wgs84` 修饰符),
而产品要问的是"**我的等时线多边形内**"。裁剪无论如何都要在 PostGIS 里做,而等时线和 `poi`
已经在同一个库。换 Mongo/ES 就变成应用层 join。字段不规整用 `raw jsonb` 解决,
与 `poi.tags` 同一套路。ES 的位置是以后做公告正文全文检索时的**二级索引**,不是主存储。

## 索引设计:一次差点骗过我自己的测量

迁移 `0009` 把 `published_at` 反范式化到位置表并建 `GIST (published_at, geom)`。
第一版测出来是 **3,956 buffers / 15.3 ms 对 14,988 / 103.2 ms**,3.8x / 6.7x,看起来是碾压。

**那个数字是假的,它把三件事混成了一件:**
1. 复合索引本身;
2. 对照用的 lab 表**还额外反范式化了 `doc_type`**,所以根本没有 join;
3. 对照用的 lab 表是 `CREATE TABLE AS` 新建的,**没有膨胀**(18 MB),而真实表经历过
   一次失败导入 + TRUNCATE + 三次重导,44 MB —— 光这一项就是接近 2x。

线索是 lab 表**列更多反而更小**。`VACUUM FULL` 后真实表 44 MB → 24 MB,再测:

| 方案 | buffers | 时间 |
|---|---|---|
| 日期过滤在主表(0005 的设计) | 13,936 | 66.1 ms |
| 日期在位置表 + `GIST(published_at, geom)` | **11,704** | **41.0 ms** |
| 同上,但查询不需要 join(只计数) | **4,128** | **12.9 ms** |
| lab:日期+类型都反范式化,无 join | 3,980 | 14.8 ms |

真实收益是 **1.6x / 16% buffers**,不是 6.7x。两个谓词都进了 Index Cond:

```
Index Cond: ((published_at >= '2026-07-01') AND (geom && i.geom))
```

剩下的开销是对全部 54,566 条公告的 hash join,不是空间扫描 —— 这从"不需要 join 时是 3.4x"
可以直接读出来。

**三条互相限制的结论,单独拿出任何一条都是错的:**
- 不是"复合 GiST 好":`0005` 在**同一张表**上选了普通 GiST,理由是 `poi` 上复合索引贵 29%。
  两者都对 —— `poi` 的前导列**不在谓词里**,这里的日期**永远在**。
- 不是"该反范式化":反范式化 + geom-only 索引是 **15,080 buffers,比不反范式化还差**,
  因为行变宽后每个堆页装的元组更少,而过滤省下的东西索引用不上。
- 真正的规则更窄:**复制一列只有在索引能用上它时才值钱;复合索引只有在前导列出现在谓词里时才值钱。**

顺带一个数字:新索引**留不住**。11,704 = 7,779 hit + 3,925 read,可重复。
`shared_buffers=128MB` 是这个项目**故意设小**的,就是为了让这类效应在 10^5 行而不是 10^9 行出现。

## 教训(与前面四个坑并列)

**对照组的每一个差异都要是你有意引入的。** 我同时改了索引、表结构和膨胀状态,然后把
全部收益算在索引头上。"vary one axis at a time" 这条规矩我在 ORS 那次是对别人的结论用的,
这次没对自己的用。
