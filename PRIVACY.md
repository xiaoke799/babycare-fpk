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

2.2 **无云服务**：应用不包含云同步、云备份或远程数据访问功能（除用户主动配置的联网 AI 或推送通知外）。

2.3 **数据控制**：您随时可以查看、编辑、删除所有数据，也可以通过导出功能备份数据。

---

### 3. 信息共享

#### 3.1 默认情况
默认情况下，应用不会与任何第三方共享您的数据。

#### 3.2 推送通知（需用户主动配置）

若在「通知设置」中填入企业微信 / 钉钉 / 飞书 / Bark / PushPlus 等 webhook 或 token，**提醒标题与内容会离开 NAS**，发送到对应第三方推送服务。请按需开启，并避免在推送正文中包含高度敏感的健康细节。

#### 3.3 AI 大模型功能（需用户主动启用）

育儿宝提供两种大模型使用模式：

##### 3.3.1 联网 API 模式（存在隐私风险，存在数据出境）

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

**数据出境提示**：若您选择的 AI 服务商服务器位于中国境外（例如 OpenAI、Kimi 等境外接口），或虽在境内但使用境外云服务节点，育儿数据将被传输至境外，构成**数据出境**。在启用前请充分了解该服务商的隐私政策，并自行评估是否需要依照《个人信息保护法》及《数据出境安全评估办法》《个人信息出境标准合同办法》履行相应的合规程序（如标准合同备案或安全评估）。本应用不对您选择的服务商所引发的合规责任承担责任。

##### 3.3.2 本地部署大模型模式（推荐，隐私优先）

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

### 5. 儿童个人信息保护（重点章节）

5.1 **年龄认定**：本应用服务的对象全部为未成年人。因此，本应用处理的儿童信息
均属于《中华人民共和国个人信息保护法》第三十一条所称的**不满十四周岁未成年人
个人信息**。

5.2 **敏感性认定**：本应用处理的儿童信息包含身高、体重、头围、体温、用药、过敏、
疾病与就诊记录等，属于第二十八条规定的**敏感个人信息（医疗健康类）**。

5.3 **监护人同意**：
- 本应用不设独立账号与密码体系，身份认证由飞牛 OS 网关承担。
- 使用者应为儿童的**父母或者其他监护人**，并在**首次启动时勾选确认**已阅读并同意
  本政策与《用户协议》（未勾选无法进入主界面）。
- 应用会在您的 NAS 本地数据库中记录同意的**时间**与**政策版本号**
  （`consent_records` 表），仅用于证明已履行告知义务；该记录不含任何儿童信息内容。
- **撤回方式**：设置页提供「撤回同意」入口，清除同意记录并同时停用 AI 数据外发
  功能。撤回不影响撤回前基于同意已进行的处理活动。

5.4 **最小必要**：本应用仅收集实现育儿记录功能所必需的信息，**不收集**身份证号、
精确地理位置、通讯录、生物识别信息等与育儿记录无关的个人信息。

5.5 **不用于商业目的**：本应用为开源工具软件，**不含广告、不设用户画像、不向任何
第三方出售或共享**儿童信息。唯一例外是您**主动配置**的推送通知与 AI 大模型服务
（见第 3 节），相关流向由您本人选择并承担。

5.6 **数据本地化**：默认配置下全部儿童信息存储在您自己的 NAS 设备中；应用本身
不建立任何服务器端用户数据库，不主动上传任何数据。

5.7 **删除宝宝档案为不可逆操作**：删除操作受安全码保护以防误删，但一旦完成，
相关儿童信息将被物理删除且无法找回，请在删除前先行导出备份。

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

7.1 本隐私政策以版本号标识，更新后的版本将在应用内「设置 → 隐私政策」页面与项目仓库同时发布。

7.2 **实质性变更**（如新增收集信息、变更共享范围、变更存储期限）时，应用会在下次启动时重新提示并要求您再次确认同意后方可继续使用。

7.3 您不同意更新后的政策时，可停止使用并删除应用数据。

---

### 8. 信息主体权利行使

8.1 您可在应用内自助行使下列权利，无需联系作者：

| 权利 | 操作路径 |
|---|---|
| 查阅 | 各记录页查看全部已录入数据 |
| 更正 | 点击任意记录进行编辑 |
| 删除 | 删除单条记录 / 删除宝宝档案（级联）/ 重置应用数据 |
| 导出 | 导出 JSON、疫苗 CSV，或生成含相册的 ZIP 备份包 |
| 撤回同意 | 设置页「撤回同意」，清除同意记录并停用 AI 数据外发 |

8.2 涉及**飞牛 OS 账户**本身的注销，请在飞牛 OS 系统中操作；本应用不持有账户凭据。

8.3 **未成年人信息主体的权利**由其监护人代为行使。监护人有权代未成年人查阅、
更正、删除其信息。

8.4 若您对权利行使有疑问，可通过下方联系方式提出，我们将在 **15 个工作日**内答复。

---

### 9. 存储期限

9.1 儿童个人信息在您使用本应用期间**持续存储于您的 NAS 设备**，应用不对数据设置
自动过期时间——删除时机由您决定。

9.2 彻底清除的路径：删除全部宝宝档案 → 重置应用数据 →（可选）清理 NAS 上的备份文件。

9.3 **备份文件同样包含全部儿童信息**：ZIP 备份、JSON 导出、CSV 导出均包含明文
数据，其保存期限由您决定。建议定期清理过期备份，勿将其长期留放在共享目录、
移动硬盘或他人可访问的位置。

9.4 使用联网 AI 功能期间，数据按对应服务商的留存政策处理，请一并向其行使删除权利。

---

### 10. 联系我们

如有任何隐私问题、权利行使请求或投诉，请通过飞牛 OS 应用中心或
GitHub 项目页面联系作者 xiaoke799。我们将在收到请求后 **15 个工作日**内答复。

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

2.2 **No Cloud Services**: The App does not include cloud synchronization, cloud backup, or remote data access features (except for online AI features or push notifications actively configured by the user).

2.3 **Data Control**: You can view, edit, and delete all data at any time, and export data for backup.

---

### 3. Information Sharing

#### 3.1 Default
By default, the App does not share your data with any third party.

#### 3.2 Push Notifications (Requires User Active Configuration)

If you enter WeChat Work / DingTalk / Feishu / Bark / PushPlus webhook URLs or tokens in notification settings, **reminder titles and content leave your NAS** and are delivered to those third-party push services. Enable only as needed and avoid including highly sensitive health details in push bodies.

#### 3.3 AI LLM Features (Requires User Active Configuration)

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
