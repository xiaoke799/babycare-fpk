# 育儿宝 (BabyCare-FPK) - 飞牛 fnOS 原生育儿应用

**语言 / Language**: [简体中文](README.md) | [English](README_EN.md)

## 简介

育儿宝是飞牛 fnOS 平台原生 NAS 育儿应用，面向使用飞牛 OS NAS 的家庭用户，主打本地私有存储，所有育儿相关数据全部保存在用户自己的 NAS 设备内，不上传第三方云端，契合当下用户对个人家庭隐私安全的诉求，打造属于家庭的私密育儿数字空间。

## 核心功能

### 📊 成长记录
- 身高、体重、头围记录，生成 WHO 生长曲线
- BMI 自动计算，生长速度追踪
- 成长对比照片管理

### 🍼 照护追踪
- 喂奶记录（母乳、配方奶、辅食分类，含奶量和哺乳侧）
- 睡眠记录（时段、质量、自动统计）
- 换尿布记录（小便/大便/混合）
- 吸奶记录、趴睡训练记录

### 🏥 健康档案
- 体检记录、体温监测、用药记录
- 过敏测试、囟门检查、出牙记录
- 疫苗管理、用药提醒

### 🎯 发育评估
- 成长里程碑记录
- ASQ 发育筛查
- 发育飞跃期追踪

### ✍️ 育儿日记
- 记录宝宝成长瞬间
- 上传对比照片
- 日记模板和智能生成

### 🤖 AI 辅助育儿
- 成长记录智能总结
- 育儿建议
- 照片内容描述
- 日记生成
- 问题答疑

### 📚 育儿知识库
- 内置喂养、睡眠、发育、健康、护理等方面知识文章
- 辅食食谱推荐

### ⏱️ 工具
- 喂奶计时器、睡眠计时器
- 数据导出/导入

## AI 功能说明

育儿宝支持接入大模型 LLM 能力，提供两种使用模式：

### 1. 联网 API 模式
调用外部在线大模型接口（如 DeepSeek、Kimi、OpenAI 等）。

⚠️ **隐私风险提示**：选择第三方联网 API 服务时，宝宝照片、成长日记、喂养记录等育儿数据会传输到对应第三方大模型服务商服务器。数据会离开您的 NAS 本地存储环境，建议处理高度敏感信息时谨慎使用。

### 2. 本地部署大模型模式（推荐，隐私优先）
大模型运行在 NAS 本机硬件，所有 AI 推理计算全部在本地完成。宝宝数据不会离开您的 NAS 设备，完整保留本地隐私优势。

详见 [隐私政策](PRIVACY.md) 和 [用户协议](USER_AGREEMENT.md)。

## 技术架构

- **后端**：Python Flask + Gunicorn + SQLite
- **前端**：原生 HTML/CSS/JavaScript（响应式设计）
- **访问方式**：飞牛 OS 网关（Unix Socket）
- **数据存储**：SQLite 数据库 + 飞牛 fnOS 数据目录
- **AI 接入**：支持 OpenAI 兼容 API 和本地 Ollama

## 项目结构

```
babycare-fpk/
├── manifest              # 应用包描述文件
├── NOTICE                # 第三方组件与数据归属声明
├── README.md             # 项目说明（中文）
├── README_EN.md          # Project intro (English)
├── USER_AGREEMENT.md     # 用户协议
├── PRIVACY.md            # 隐私政策
├── LICENSE               # Apache-2.0 开源协议
├── cmd/                  # 生命周期脚本（main/install/upgrade/uninstall/config）
├── config/               # 运行权限与资源配置
├── wizard/               # 安装/卸载向导
├── app/
│   ├── backend/          # Python 后端
│   │   ├── server.py     # Flask 应用入口
│   │   ├── ai_engine.py  # AI 引擎（本地规则引擎 + 大模型接入）
│   │   ├── growth_utils.py / who_data.py   # WHO 生长标准计算与数据
│   │   ├── blueprints/   # 路由模块
│   │   └── vendor/       # 离线依赖（Flask 及其依赖，见 NOTICE）
│   ├── frontend/         # 前端页面（原生 HTML/CSS/JS，响应式）
│   ├── ui/               # fnOS 桌面入口
│   └── data/             # 数据目录（运行时生成）
├── ICON.PNG              # 应用图标
└── ICON_256.PNG          # 应用图标 (256x256)
```

## 本地开发

```bash
cd app/backend
pip install -r requirements.txt
python3 server.py    # 默认 http://localhost:8090，可用环境变量 BABYCARE_PORT 覆盖
```

本地开发模式默认关闭无口令登录，需设置环境变量 `BABYCARE_DEV_AUTH=1`
后调用 `POST /api/auth/login` 登录；fnOS 生产环境由网关认证，无需此开关。

## 打包发布

```bash
fnpack build -d babycare-fpk
# 生成 babycare-fpk.fpk
```

注意：在 Windows 上打包后，需将包内生命周期脚本的执行位修复为 0755
（Windows 文件系统不保留 Unix 执行位），否则脚本在 fnOS 设备上无法运行。

## 开源协议

本项目采用 [Apache License 2.0](LICENSE) 开源协议。Copyright 2026 xiaoke799。

## 隐私政策

详见 [隐私政策](PRIVACY.md)。所有数据均本地存储，使用在线 AI 功能时部分数据可能发送至第三方厂商。

## 用户协议

详见 [用户协议](USER_AGREEMENT.md)。

## 数据合规

- WHO 生长参考数据来源：[世界卫生组织儿童生长标准](https://www.who.int/tools/child-growth-standards)
- 健康百科内容仅供学习参考，不构成医疗建议
- 使用联网 AI 功能时请遵守对应 AI 服务商的使用条款

## 版本说明

版本号以 manifest 与 git tag 为准；git 提交与包版本相互独立，
多个开发节点可合并为一个发布版本。

- v0.0.1 (2026-09-13) - 首个开源版本（git tag v0.0.1）
