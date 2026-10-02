# BabyCare 育儿宝 · Home Assistant 集成

把 **babycare-fpk**（飞牛 fnOS 育儿应用）接入 Home Assistant，实现智能家居联动。

## 功能

- 传感器：每宝宝「最近喂奶 / 睡眠 / 尿布 / 身高体重」「今日喂奶次数 / 奶量」
- 服务：
  - `babycare.add_feeding` / `add_sleep` / `add_diaper` / `add_growth` — 写入记录
  - `babycare.start_timer` / `stop_timer` — 启停计时器（停止时可自动生成记录）
  - `babycare.delete_last_entry` — 删除最近一条记录
- 开关：每宝宝「喂奶计时 / 睡眠计时」，开 = 启动计时器，关 = 停止并写入记录

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

## 与 Baby Buddy 集成的差异

- 对接自有 NAS 应用，数据留在本地
- Token 鉴权，无 Baby Buddy Django API
- 传感器字段按育儿宝接口对齐（start_time / change_time / record_date）
