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
  manifest.json
  sensor.py
  services.py
  services.yaml
  switch.py
```

重启 HA → 设置 → 设备与服务 → 添加集成 → **BabyCare 育儿宝**

| 字段 | 说明 |
|------|------|
| Host | 如 `http://192.168.1.10` 或 `http://nas.local` |
| Port | 默认 `8090`（fnOS 网关请填网关端口，Path 填 `/app/babycare-fpk`） |
| Path | 反代子路径，默认空 |
| API Key | 育儿宝生成的 Token（`bcb_...`） |

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

## 与 Baby Buddy 集成的差异

- 对接自有 NAS 应用，数据留在本地
- Token 鉴权，无 Baby Buddy Django API
- 传感器字段按育儿宝接口对齐（start_time / change_time / record_date）
