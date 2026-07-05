# 宝可梦 TCG 童年卡数据设计

## 背景

当前要整理的是用户童年接触过的中文黄边旧模板卡。公开照片显示它们主要对应早期官方 Pokemon TCG 母版，而不是官方简体中文版。当前确认最接近的官方范围先限定为：

- Base Set
- Jungle
- Fossil
- Team Rocket

Gym Heroes / Gym Challenge 存在训练家头像和道馆专属版式，暂不纳入本批数据。

## 存储原则

这批数据分为两层：

1. 原始层：尽量完整保存从 PkmnCards 抓取的官方母版数据和图片缓存，避免后续反复爬虫。
2. 派生层：本地编目编号、去重、排除规则、童年中文卡匹配关系都作为可重算结果，不写死为不可变事实。

原始数据是外部可再生快照，不直接进入仓库源码目录。默认写入：

```text
CODEYUN_DATA_DIR/pokemon_tcg/childhood_base_jungle_fossil_rocket/
```

## 目录结构

```text
pokemon_tcg/
  childhood_base_jungle_fossil_rocket/
    manifest.json
    raw_cards.json
    catalog_policy_default.json
    progress.json
    images/
      base-set/
      jungle/
      fossil/
      team-rocket/
```

## 原始卡字段

`raw_cards.json` 每条记录对应一张官方卡，不做物种去重：

- `source`: 固定为 `pkmncards`
- `source_url`
- `source_card_slug`
- `set_name`
- `set_slug`
- `official_set_code`
- `official_number`
- `official_total`
- `official_id`
- `official_name`
- `display_title`
- `pokemon_species`
- `hp`
- `color`
- `stage`
- `evolves_from`
- `evolves_into`
- `is_dark`
- `attacks_text`
- `weakness_text`
- `resistance_text`
- `retreat_cost`
- `illustrator_text`
- `rarity`
- `release_date_text`
- `flavor_text`
- `image_url`
- `local_image_path`
- `image_sha256`
- `image_bytes`
- `raw_text`
- `fetched_at`

## 编目策略

本地编号不要覆盖官方编号。推荐保留两个体系：

- 官方编号：`official_id`，例如 `RO-35`、`FO-36`
- 本地编号：由编目策略派生，例如 `catalog_no` + `variant_no`

展示编号建议：

```text
variant_no == 1 -> catalog_no
variant_no > 1  -> catalog_no.variant_no
```

例如：

```text
25    Flareon · Jungle #3
25.2  Flareon · Jungle #19
25.3  Dark Flareon · Team Rocket #35
```

这些编号属于派生层，未来可以按新规则重算。

## 未来落库建议

如果后续要做 CodeYun 页面检索、收藏、人工标注，再新增正式表：

- `pokemon_tcg_card`：官方母版卡索引，指向快照中的原始记录。
- `pokemon_tcg_card_image`：本地图片缓存路径、hash、尺寸。
- `pokemon_tcg_catalog_entry`：某个编目策略下的编号结果。
- `pokemon_tcg_childhood_match`：童年中文卡照片/文字与官方母版的人工匹配关系。

图片不直接入库，只保存相对路径和 hash。

## 当前抓取脚本

```text
uv run python scripts/download_pokemon_tcg_catalog.py
```

常用参数：

```text
--force-images       重新下载已存在图片
--no-images          只抓结构化数据
--sets team-rocket   只抓某些集合
```

脚本可重复运行。已有图片会按路径跳过，并重新计算 hash 写入 JSON。
