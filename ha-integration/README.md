# BabyCare 育儿宝 · Home Assistant 集成

把 **babycare-fpk**（飞牛 fnOS 育儿应用）接入 Home Assistant，实现智能家居联动。

## 功能

- 传感器：每宝宝「最近喂奶 / 睡眠 / 尿布 / 身高体重」「今日喂奶次数 / 奶量 / 换尿布次数」
- 按钮：每宝宝「记亲喂 / 记换尿布」——按一下写一条记录（适合仪表盘卡片、NFC 贴纸、语音助手）
- 服务：
  - `babycare.add_feeding` / `add_sleep` / `add_diaper` / `add_growth` — 写入记录
  - `babycare.start_timer` / `stop_timer` — 启停计时器（停止时可自动生成记录）
  - `babycare.delete_last_entry` — 删除最近一条记录
- 开关：每宝宝「喂奶计时 / 睡眠计时」，开 = 启动计时器，关 = 停止并写入记录

> 快捷按钮只做「无参数即完整」的记录：亲喂本无奶量、换尿布无需额外字段。
> 奶瓶喂需要指定量，请用 `babycare.add_feeding` 服务或喂奶计时开关。

## 前置条件

1. 育儿宝已安装并可访问（局域网 HTTP 或 fnOS 网关）
2. 在育儿宝生成 **API Token**（设置 → 智能家居集成）

## 安装

将 `custom_components/babycare` 复制到 Home Assistant 的 `config/custom_components/`：

```text
config/custom_components/babycare/
  __init__.py
  client.py
  config_flow.py
  const.py
  coordinator.py
  entity.py          # 设备注册（一个宝宝 = 一个设备）
  manifest.json
  sensor.py
  services.py
  services.yaml
  strings.json       # 配置流程文案（源文件）
  switch.py
  translations/
    en.json
    zh-Hans.json
```

重启 HA → 设置 → 设备与服务 → 添加集成 → **BabyCare 育儿宝**

| 字段 | 说明 |
|------|------|
| Host | 如 `http://192.168.1.10` 或 `http://nas.local` |
| Port | 默认 `8090`（fnOS 网关请填网关端口，Path 填 `/app/babycare-fpk`） |
| Path | 反代子路径，默认空 |
| API Key | 育儿宝生成的 Token（`bcb_...`） |

## 设备与实体

每个宝宝在 HA 里是一个**设备**，其下挂着该宝宝的传感器与开关：

- 传感器：最近喂奶 / 最近睡眠 / 最近尿布 / 最近身高体重 / 今日喂奶次数 / 今日奶量
- 开关：喂奶计时 / 睡眠计时（开 = 启动计时器，关 = 停止并写入记录）

实体 ID 基于 `babycare_<宝宝id>_<种类>`，重启或重新配置不会变，
所以自动化里引用的实体不会因为重装集成而失效。

## 自动化示例

```yaml
service: babycare.add_feeding
data:
  baby_id: 1
  type: bottle
  amount: 120
  note: HA 自动化写入
```

```yaml
service: babycare.stop_timer
data:
  timer_id: 3
  create_record: true
```

## 鉴权

请求头：`Authorization: Bearer <token>`（也支持 `Token <token>`）  
Token 仅存哈希；泄露后请在育儿宝重新生成或吊销。  
集成 Token 可读数据、写喂养/睡眠/尿布/生长/计时记录，**不能**改设置、删宝宝或动备份。

### Token 失效怎么办

在育儿宝里重新生成或吊销 Token 后，HA 会自动弹窗让你重新填新 Token，
**不需要删掉集成重加**（重加会丢实体 ID，自动化里引用的实体要重做）。

如果没弹窗、实体显示不可用，去「设置 → 设备与服务 → BabyCare 育儿宝」点「配置」重新填一次即可。

## 按官方开发文档核对过的点

本集成对照 Home Assistant 官方开发文档（`developers.home-assistant.io`
的文档源码，见 `D:\育儿板块\developers.home-assistant-master`）核对过：

| 项目 | 文档依据 | 现状 |
|------|---------|------|
| `integration_type: hub` | `creating_integration_manifest.md`：hub = 一个入口管**多个设备/服务** | ✅ 本集成一个 config entry 管多个宝宝（每宝宝一设备） |
| 服务注册在 `async_setup` | `core/integration-quality-scale/rules/action-setup.md` | ✅ 不在 `async_setup_entry`，否则 entry 未加载时用户的自动化无法校验 |
| 服务未就绪时抛 `HomeAssistantError` + `translation_key` | 同上：「raise `ServiceValidationError`」 | ✅ 用户看到「集成未加载完成」而不是泛化的内部错误 |
| 翻译三件套齐全 | `internationalization/core.md` | ✅ `strings.json` + `translations/en.json` + `zh-Hans.json`，缺一份界面会直接显示原始 key |
| 数值传感器带 `native_unit_of_measurement` | `core/entity/sensor.md` | ✅ 奶量用 `mL` |
| `state_class: MEASUREMENT` | 同上：累计增量才用 `TOTAL_INCREASING` | ✅ 今日次数每天归零，是统计量不是单调计数器 |

### 刻意**不**设 `device_class: timestamp` 的原因

「最近喂奶 / 睡眠 / 尿布」四个实体显示的是时间，官方对 `TIMESTAMP` 的要求是
「`native_value` 必须返回**带时区**的 `datetime`」。而后端返回的是
**naive 本地时间字符串**（实测）：

```
feeding.start_time = '2026-10-03 10:00:00'
diaper.change_time  = '2026-10-03 11:00:00'
growth.record_date  = '2026-10-03'      ← 还是纯日期
```

三者都不带时区。硬加 `TIMESTAMP` 会让 HA 按 UTC 解释、算出错的时间，
而界面上看不出任何异常 —— 反而比不加更糟。
等后端改成带时区的 ISO8601（末尾 `+08:00`）再改，那时才是真收益。
（另外 `TIMESTAMP` 与 `state_class` 互斥，别两个一起加。）

以上几条都写进了 `verify_ha_integration.py` 的断言，改坏了会立刻红。

## 仪表盘卡片示例

实体卡片（一行看全今日状态 + 手动记录入口）：

```yaml
type: entities
title: 宝宝今日
entities:
  - sensor.babycare_today_feeding_count   # 今日喂奶次数
  - sensor.babycare_today_feeding_ml      # 今日奶量 ml
  - sensor.babycare_today_diaper_count    # 今日换尿布次数
  - sensor.babycare_last_feeding          # 最近喂奶
  - button.babycare_ji_qin_wei            # 记亲喂（按一下记录一次）
  - button.babycare_ji_huan_niao_bu       # 记换尿布
  - switch.babycare_wei_nai_ji_shi        # 喂奶计时
```

NFC 贴纸 + 快捷按钮：把「记换尿布」按钮绑定到 NFC 标签贴在尿布台，
手机一碰即完成记录；语音助手说「打开 记亲喂」也可以触发（button 实体
的 turn_on 即按下）。

## 与 Baby Buddy 集成的差异

- 对接自有 NAS 应用，数据留在本地
- Token 鉴权，无 Baby Buddy Django API
- 传感器字段按育儿宝接口对齐（start_time / change_time / record_date）
