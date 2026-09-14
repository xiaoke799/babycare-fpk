# 用户协议 / User Agreement

**生效日期 / Effective Date**：2026-08-29  
**版本 / Version**：1.1

---

## 中文版本

### 1. 概述

欢迎使用**育儿宝**（以下简称"本应用"）。本应用是飞牛 fnOS 平台原生 NAS 育儿应用，面向使用飞牛 OS NAS 的家庭用户，主打本地私有存储，所有育儿相关数据全部保存在用户自己的 NAS 设备内，不上传第三方云端，契合当下用户对个人家庭隐私安全的诉求，打造属于家庭的私密育儿数字空间。

请您（"用户"）在使用本应用前仔细阅读本协议。一旦您开始使用本应用，即表示您已阅读、理解并同意接受本协议条款的约束。

### 2. 功能说明

本应用提供以下核心功能：

- **多宝宝管理**：创建和管理多位宝宝档案，支持轻松切换
- **成长记录**：记录身高、体重、头围，生成 WHO 生长曲线
- **照护追踪**：记录喂奶、睡眠、换尿布、吸奶、趴睡训练等日常照护
- **健康档案**：管理体检记录、体温、用药、过敏测试、囟门检查、出牙记录
- **发育评估**：成长里程碑、ASQ 发育筛查、发育飞跃期
- **育儿日记**：记录宝宝成长瞬间，上传对比照片
- **育儿知识库**：内置喂养、睡眠、发育、健康、护理等方面知识文章
- **AI 辅助育儿**：接入大模型 LLM 能力，提供成长记录智能总结、育儿建议、照片内容描述、日记生成、问题答疑等功能
- **计时器**：喂奶、睡眠等计时工具
- **数据导出**：支持全量数据导出和导入

### 3. 大模型功能（AI 服务）

育儿宝支持接入大模型 LLM 能力，提供两种使用模式：

#### 3.1 联网 API 模式

调用外部在线大模型接口（如 DeepSeek、Kimi、OpenAI 等）。

**⚠️ 隐私风险提示**：当选择第三方联网 API 服务时，宝宝照片、成长日记、喂养记录、家庭相关文本等育儿数据，会传输到对应第三方大模型服务商服务器用于 AI 计算。

- 数据会离开您的 NAS 本地存储环境，将受第三方服务商隐私政策约束
- 由此产生的数据泄露、隐私相关风险，需要由用户自行评估并承担
- 应用仅做接口转发，不会收集、留存您提交给 API 的内容，但无法控制外部服务商的数据处理行为

**建议**：处理高度敏感家庭育儿信息时，谨慎使用联网 API 模式。

#### 3.2 本地部署大模型模式（推荐，隐私优先）

大模型运行在 NAS 本机硬件，所有 AI 推理计算全部在本地完成。

- 宝宝的照片、文字记录等全部数据不会离开您的 NAS 设备，不会外传任何第三方
- 完整保留本地隐私优势，完全契合育儿宝"数据本地私有"的产品设计初衷
- 限制：受 NAS 硬件算力、内存影响，模型大小、推理速度会受硬件配置制约

### 4. 数据与隐私

4.1 **本地存储**：所有育儿数据均存储在您 NAS 设备的本地 SQLite 数据库中，不会自动上传至任何服务器。

4.2 **数据控制**：您随时可以查看、编辑、删除所有数据，也可以通过导出功能备份数据。

4.3 **AI 功能数据**：如果您启用了在线 AI 大模型服务，相关数据可能发送至您选择的 AI 厂商。启用此功能前，请仔细阅读对应第 3.1 节的隐私风险提示和 AI 厂商的隐私政策。

### 5. 使用规则

5.1 本应用仅供个人和家庭使用，不得用于商业目的。

5.2 您应当妥善保管自己的 NAS 设备和账户，防止他人未经授权访问您的数据。

5.3 本应用提供的育儿知识仅供参考，不构成医疗建议。如有健康问题，请咨询专业医疗人员。

5.4 使用大模型功能时，您应当遵守所接入 AI 服务的使用条款和相关法律法规。

### 6. 免责声明

6.1 本应用按"现状"提供，不保证无错误或中断。

6.2 作者不对因使用本应用导致的任何直接、间接损失承担责任。

6.3 本应用提供的生长曲线和发育评估基于 WHO 标准，仅供参考，不构成医疗诊断。

6.4 使用联网 AI 功能产生的服务质量、响应速度、结果准确性取决于第三方 AI 服务商，作者不对此承担责任。

6.5 使用本地部署大模型功能所需的硬件配置、模型获取由用户自行负责。

### 7. 协议变更

本协议可能会不时更新。更新后的协议将在应用文档中发布。

### 8. 联系我们

如有任何问题，请通过飞牛 OS 应用中心或 GitHub 项目页面联系作者 xiaoke799。

---

## English Version

### 1. Overview

Welcome to **BabyCare** (hereinafter referred to as "the App"). This is a native NAS parenting application for the Feiniu fnOS platform, designed for families using Feiniu OS NAS devices. The App focuses on local private storage — all parenting-related data is stored entirely on the user's own NAS device, not uploaded to any third-party cloud, meeting the privacy and security needs of modern families and creating a private digital parenting space for your family.

Please read this agreement carefully before using the App. By using the App, you acknowledge that you have read, understood, and agree to be bound by the terms of this agreement.

### 2. Features

The App provides the following core features:

- **Multiple Baby Profiles**: Create and manage profiles for multiple babies with easy switching
- **Growth Recording**: Record height, weight, head circumference with WHO growth curves
- **Care Tracking**: Log feeding, sleep, diaper changes, pumping, tummy time
- **Health Records**: Manage checkups, temperature, medications, allergy tests, fontanelle exams, teething
- **Development Assessment**: Milestones, ASQ screening, developmental leaps
- **Parenting Journal**: Record precious moments and upload comparison photos
- **Knowledge Base**: Built-in parenting guides and recipes
- **AI-Assisted Parenting**: LLM capabilities for growth summary, parenting advice, photo description, journal generation, Q&A
- **Timers**: Tools for feeding, sleep, and more
- **Data Export**: Full data export and import support

### 3. Large Model Features (AI Services)

The App supports LLM integration with two usage modes:

#### 3.1 Online API Mode

Calls external online LLM services (e.g., DeepSeek, Kimi, OpenAI, etc.).

**⚠️ Privacy Risk Notice**: When selecting third-party online API services, baby photos, growth journals, feeding records, family-related text and other parenting data will be transmitted to the corresponding third-party LLM service provider's servers for AI computation.

- Data will leave your NAS local storage environment and be subject to the third-party service provider's privacy policy
- Risks of data leakage and privacy issues arising therefrom shall be assessed and borne by the user
- The App only acts as an interface forwarder and does not collect or retain content you submit to the API, but cannot control the data processing behavior of external service providers

**Recommendation**: Exercise caution when using online API mode for highly sensitive family parenting information.

#### 3.2 Local Deployment Mode (Recommended, Privacy-First)

The LLM runs on NAS local hardware, with all AI inference computation completed locally.

- Baby photos, text records and all other data will not leave your NAS device and will not be transmitted to any third-party
- Fully preserves the local privacy advantage, completely aligning with the "data local private" product design philosophy
- Limitation: Model size and inference speed are subject to hardware configuration constraints

### 4. Data and Privacy

4.1 **Local Storage**: All parenting data is stored in a local SQLite database on your NAS device and is not automatically uploaded to any server.

4.2 **Data Control**: You can view, edit, and delete all data at any time, and export data for backup.

4.3 **AI Feature Data**: If you enable online AI LLM services, relevant data may be sent to the AI provider of your choice. Before enabling this feature, please carefully read the privacy risk notice in Section 3.1 and the AI provider's privacy policy.

### 5. Rules of Use

5.1 The App is for personal and family use only and may not be used for commercial purposes.

5.2 You are responsible for securing your NAS device and account to prevent unauthorized access to your data.

5.3 The parenting knowledge provided in the App is for reference only and does not constitute medical advice. Consult a healthcare professional for health concerns.

5.4 When using LLM features, you shall comply with the terms of use and relevant laws and regulations of the connected AI service.

### 6. Disclaimer

6.1 The App is provided "as is" without warranty of any kind, either expressed or implied.

6.2 The author shall not be liable for any direct or indirect damages arising from the use of the App.

6.3 Growth curves and developmental assessments provided by the App are based on WHO standards and are for reference only; they do not constitute medical diagnosis.

6.4 Service quality, response speed, and result accuracy of online AI features depend on third-party AI service providers; the author assumes no responsibility therefor.

6.5 Hardware configuration and model acquisition required for local deployment LLM features are the user's own responsibility.

### 7. Agreement Changes

This agreement may be updated from time to time. The updated agreement will be published in the App documentation.

### 8. Contact Us

For any questions, please contact the author xiaoke799 via the Feiniu OS App Center or GitHub project page.

---

## 中英文冲突处理 / Conflict Resolution

如中英文版本存在歧义或冲突，以**中文版本**为准。

In the event of any ambiguity or conflict between the Chinese and English versions, the **Chinese version** shall prevail.

---

**继续使用即表示您同意本协议。 / By continuing to use the App, you agree to this agreement.**
