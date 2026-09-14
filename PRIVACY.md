# 隐私政策 / Privacy Policy

**生效日期 / Effective Date**：2026-08-29  
**项目名称 / Project**：育儿宝 (babycare-fpk)  
**作者 / Author**：xiaoke799

---

## 中文版本

### 1. 信息收集

育儿宝是一款本地运行的 NAS 应用，我们收集以下信息以提供服务：

#### 1.1 宝宝基本信息
- 姓名（或昵称）
- 生日
- 性别
- 头像照片（可选）

#### 1.2 成长数据
- 身高、体重、头围
- BMI 指数

#### 1.3 日常照护记录
- 喂奶记录（时间、奶量、哺乳侧）
- 睡眠记录（时间、时长、质量）
- 换尿布记录
- 吸奶记录
- 趴睡训练记录

#### 1.4 健康档案
- 体检记录（医院、医生、检查结果）
- 体温记录
- 用药记录
- 过敏测试记录
- 囟门检查记录

#### 1.5 发育数据
- 成长里程碑
- ASQ 发育筛查评分
- 出牙记录
- 发育飞跃期记录

#### 1.6 内容记录
- 育儿日记
- 成长对比照片

---

### 2. 信息存储

2.1 **本地存储**：所有数据均存储在您 NAS 设备的本地 SQLite 数据库中，不会自动上传至任何服务器。

2.2 **无云服务**：应用不包含云同步、云备份或远程数据访问功能（除用户主动配置的联网 AI 功能外）。

2.3 **数据控制**：您随时可以查看、编辑、删除所有数据，也可以通过导出功能备份数据。

---

### 3. 信息共享

#### 3.1 默认情况
默认情况下，应用不会与任何第三方共享您的数据。

#### 3.2 AI 大模型功能（需用户主动启用）

育儿宝提供两种大模型使用模式：

##### 3.2.1 联网 API 模式（存在隐私风险）

如果您在设置中启用了在线 AI 大模型服务，以下信息可能发送至您选择的 AI 厂商：

- 宝宝月龄和基本信息
- 最新身高、体重数据
- 成长记录摘要
- 您提出的问题内容
- 您选择发送给 AI 的照片

**⚠️ 重要提示**：
- 启用此功能前，请仔细阅读对应 AI 厂商的隐私政策
- 数据会离开您的 NAS 本地存储环境，将受第三方服务商隐私政策约束
- 由此产生的数据泄露、隐私相关风险，需要由用户自行评估并承担
- 应用仅做接口转发，不会收集、留存您提交给 API 的内容
- 建议优先使用本地部署大模型方案以最大限度保护隐私

##### 3.2.2 本地部署大模型模式（推荐，隐私优先）

如果您的飞牛 OS NAS 硬件性能满足大模型运行条件，强烈建议使用本地部署 LLM 方案：

- 大模型运行在 NAS 本机硬件，所有 AI 推理计算全部在本地完成
- 宝宝的照片、文字记录等全部数据不会离开你的 NAS 设备
- 完整保留本地隐私优势，完全契合育儿宝"数据本地私有"的产品设计初衷

---

### 4. 数据安全

4.1 应用运行在您的本地 NAS 设备上，数据存储于本地文件系统。

4.2 建议采取以下措施保护您的数据安全：
- 设置 NAS 设备访问密码
- 定期使用导出功能备份数据
- 限制 NAS 设备的网络访问权限
- 使用强密码保护飞牛 OS 账户

---

### 5. 儿童隐私

5.1 本应用涉及儿童（婴幼儿）个人信息处理。

5.2 本应用作为本地工具软件，数据不出设备，无需联网即可使用全部核心功能。

5.3 家长或监护人是儿童数据的唯一控制者，应当妥善管理所录入的儿童信息。

5.4 本应用不会主动收集儿童信息，所有数据均由家长或监护人手动录入。

---

### 6. 数据删除

6.1 您可以通过以下方式删除数据：
- 在应用中删除单个记录
- 删除宝宝档案（将级联删除所有相关数据）
- 通过系统设置重置应用数据
- 卸载应用（可选择保留或删除数据）

6.2 使用联网 AI 功能时发送至第三方服务商的数据，其删除受该服务商隐私政策约束，请直接联系对应服务商处理。

---

### 7. 政策更新

本隐私政策可能会不时更新。更新后的政策将在应用文档中发布。

---

### 8. 联系我们

如有任何隐私问题，请通过飞牛 OS 应用中心或 GitHub 项目页面联系作者 xiaoke799。

---

## English Version

### 1. Information Collection

BabyCare is a locally-run NAS application. We collect the following information to provide services:

#### 1.1 Baby Basic Information
- Name (or nickname)
- Birthday
- Gender
- Avatar photo (optional)

#### 1.2 Growth Data
- Height, weight, head circumference
- BMI index

#### 1.3 Daily Care Records
- Feeding records (time, amount, nursing side)
- Sleep records (time, duration, quality)
- Diaper change records
- Pumping records
- Tummy time records

#### 1.4 Health Records
- Checkup records (hospital, doctor, results)
- Temperature records
- Medication records
- Allergy test records
- Fontanelle exam records

#### 1.5 Development Data
- Growth milestones
- ASQ screening scores
- Teething records
- Developmental leap records

#### 1.6 Content Records
- Parenting journals
- Growth comparison photos

---

### 2. Information Storage

2.1 **Local Storage**: All data is stored in a local SQLite database on your NAS device and is not automatically uploaded to any server.

2.2 **No Cloud Services**: The App does not include cloud synchronization, cloud backup, or remote data access features (except for online AI features actively configured by the user).

2.3 **Data Control**: You can view, edit, and delete all data at any time, and export data for backup.

---

### 3. Information Sharing

#### 3.1 Default
By default, the App does not share your data with any third party.

#### 3.2 AI LLM Features (Requires User Active Enumeration)

The App provides two LLM usage modes:

##### 3.2.1 Online API Mode (Privacy Risks)

If you enable online AI LLM services in settings, the following information may be sent to the AI provider of your choice:

- Baby age and basic information
- Latest height and weight data
- Growth record summaries
- Content of your questions
- Photos you choose to send to AI

**⚠️ Important Notice**:
- Please carefully read the privacy policy of the corresponding AI provider before enabling this feature
- Data will leave your NAS local storage environment and be subject to the third-party service provider's privacy policy
- Risks of data leakage and privacy issues arising therefrom shall be assessed and borne by the user
- The App only acts as an interface forwarder and does not collect or retain content you submit to the API
- It is recommended to prioritize local deployment LLM solutions to maximize privacy protection

##### 3.2.2 Local Deployment Mode (Recommended, Privacy-First)

If your Feiniu OS NAS hardware performance meets LLM operation requirements, it is strongly recommended to use the local deployment LLM solution:

- The LLM runs on NAS local hardware, with all AI inference computation completed locally
- Baby photos, text records and all other data will not leave your NAS device
- Fully preserves the local privacy advantage, completely aligning with the "data local private" product design philosophy

---

### 4. Data Security

4.1 The App runs on your local NAS device, with data stored in the local file system.

4.2 It is recommended to take the following measures to protect your data security:
- Set NAS device access passwords
- Regularly use the export function to backup data
- Restrict network access permissions for NAS devices
- Use strong passwords to protect Feiniu OS accounts

---

### 5. Children's Privacy

5.1 This application involves the processing of personal information of children (infants and toddlers).

5.2 As a local tool application, data does not leave the device; all core features can be used without internet connection.

5.3 Parents or guardians are the sole controllers of children's data and should properly manage the children's information entered.

5.4 This application does not actively collect children's information; all data is manually entered by parents or guardians.

---

### 6. Data Deletion

6.1 You can delete data in the following ways:
- Delete individual records in the App
- Delete baby profiles (will cascade delete all related data)
- Reset App data through system settings
- Uninstall the App (can choose to keep or delete data)

6.2 Data sent to third-party service providers when using online AI features is subject to deletion per that service provider's privacy policy; please contact the corresponding service provider directly.

---

### 7. Policy Updates

This privacy policy may be updated from time to time. The updated policy will be published in the App documentation.

---

### 8. Contact Us

For any privacy questions, please contact the author xiaoke799 via the Feiniu OS App Center or GitHub project page.

---

## 中英文冲突处理 / Conflict Resolution

如中英文版本存在歧义或冲突，以**中文版本**为准。

In the event of any ambiguity or conflict between the Chinese and English versions, the **Chinese version** shall prevail.
