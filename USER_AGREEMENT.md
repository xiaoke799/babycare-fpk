# 用户协议 / User Agreement

**生效日期 / Effective Date**：2026-08-29  
**版本 / Version**：1.1

---

## 中文版本

### 1. 概述

> **服务性质声明**：本应用是**育儿记录工具**，**不是医疗器械，不是医疗服务，不提供诊断、治疗或用药建议**。应用内出现的曲线解读、体温提示、疫苗排程、筛查结论等一切内容，均为基于公开参考数据的**信息展示与记录辅助**，不构成医疗意见。

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

5.5 **核实义务**：应用内的 AI 建议、曲线解读、疫苗排程提示、发育评估结论均由算法基于您录入的数据生成，可能不准确、不完整或不适用于您的宝宝。**您在据此做出任何照护决定前，应当自行核实**；对健康存疑时，应当及时咨询持证医疗人员。

5.6 **用药与治疗**：应用记录用药，但**不判断用药剂量、不判断是否需要用药、不提供替代就医的建议**。一切用药与治疗请遵医嘱。

5.7 **监护人**：使用者应为儿童的父母或其他监护人（见《隐私政策》第 5 节）。

### 6. 免责声明

6.1 本应用按"现状"提供，不保证无错误或中断。

6.2 **责任限额**：在法律允许的最大范围内，作者对因使用或无法使用本应用产生的任何直接损失，赔偿总额以您就本应用向作者实际支付的费用为限（本应用免费分发，该金额通常为 0）；对间接损失、附带损失、利润损失或数据损失，作者**不承担责任**。

6.2.1 上述限制不适用于作者故意或重大过失的情形，亦不适用于依法不得限制或免除的责任。

6.2.2 **开源免责说明**：本应用依据 Apache License 2.0 开源分发，该协议第 7、8 条已明确「按现状提供、不担保」。开源许可所能免除的是**作者的责任**，**不免除您作为使用者的责任**：照搬本应用数据做出的照护决定及其后果，由您本人承担。

6.3 **生长曲线与百分位解读**：本应用提供的生长曲线、BMI、头围及各项百分位基于 WHO 儿童生长标准（LMS 方法）计算得出，**仅供参考，不构成医疗诊断**。百分位高低不代表健康与否，单次数值偏离不代表异常，是否需要干预须由医疗人员结合喂养史、疾病史与体格检查判断。

6.4 **疫苗接种计划**：应用内置的疫苗排程表依据国家免疫规划疫苗儿童免疫程序整理，并允许您自行调整，**仅供参考，不构成接种医嘱**。各地免疫规划与实际接种建议可能不同（自费疫苗、特殊疾病儿童、提前接种等），**请以当地预防接种门诊医嘱与通知为准**。因未按医嘱接种而产生的后果，由用户自行承担。

6.5 **体温与发热判断**：应用中关于「是否发热」「体温偏高」的提示基于常见参考阈值（如腋温 37.3℃ / 耳温 38℃）自动生成，**仅供参考，不构成发热诊断**。同一温度在不同部位、不同年龄、不同状态下意义不同，**是否发热、是否需要用药或就医，请以医疗人员判断为准**。婴幼儿发热尤其 3 个月以下出现发热时，请立即就医，不要依赖本应用的提示。

6.6 **辅食与过敏**：应用的辅食过敏 3 天测试记录、过敏史登记仅为记录工具，**不构成过敏诊断或饮食建议**。出现过敏反应请及时就医，不要通过逐步添加食材的方式自行试探严重过敏。

6.7 **发育评估与筛查记录**：应用提供的里程碑参考月龄、ASQ 筛查结果登记、发育飞跃期记录**不能代替专业筛查与评估**。ASQ 问卷为受版权保护的标准化测评工具，应用**不内置其题目与评分算法**，仅供用户按已购问卷的结果登记。任何发育疑虑请前往儿童保健科或儿科评估。

6.8 **看诊摘要**：应用生成的「看诊摘要 / 打印报告」仅为已录入记录的汇总打印件，便于家长向医生陈述，**不是病历、不是诊断文件**。

6.9 使用联网 AI 功能产生的服务质量、响应速度、结果准确性取决于第三方 AI 服务商，作者不对此承担责任。

6.10 使用本地部署大模型功能所需的硬件配置、模型获取由用户自行负责。

### 6A. 知识产权

6A.1 **软件代码**：本应用源代码以 Apache License 2.0 开源，版权归作者 xiaoke799
所有。您可自由使用、修改、分发，商业使用亦获许可（须保留许可与声明）。

6A.2 **用户内容**：您录入的记录、日记、照片等**归您所有**。作者不主张对用户
内容的任何所有权，仅在您主动启用云端 AI 功能时，为完成您的请求所必需，
将您指定的内容传输至您选择的第三方服务商处理。

6A.3 **第三方数据与内容**：本应用内含的 WHO 生长标准参考数据、育儿知识内容、
健康百科等，著作权归各自权利人所有（详见项目 NOTICE 文件），其使用受相应许可
约束；这些内容同样不构成医疗建议。

6A.4 **反馈**：您主动提出的改进建议，您同意作者可无偿使用而不署名。

---

### 6B. 适用法律与争议解决

6B.1 本协议适用**中华人民共和国法律**（不含港澳台地区法律）。

6B.2 因本协议产生的争议，双方应首先友好协商；协商不成的，任一方可向
**作者所在地有管辖权的人民法院**提起诉讼。

6B.3 **医疗纠纷特别提示**：若您因依赖本应用的提示而发生健康损害，请立即就医，
并保留就诊记录。本应用不构成医疗服务提供者，不承担医疗责任；此类争议
应通过医疗损害相关法律途径解决。

---

### 7. 协议变更

7.1 本协议以版本号标识，更新后将在应用内「设置 → 用户协议」与项目仓库同时发布。

7.2 **实质性变更**时，应用将在下次启动时重新提示并要求您再次确认同意。

7.3 您不同意更新后的协议时，可停止使用并删除应用数据。

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
