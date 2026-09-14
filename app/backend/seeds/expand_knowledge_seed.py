# SPDX-License-Identifier: Apache-2.0
# 知识库种子数据扩充脚本
# 用法：python expand_knowledge_seed.py
# 说明：把本脚本中 NEW_* 列表的条目追加进对应的 seeds/*.json。
#       按 title 去重（已存在的同名条目不重复写入），写回时保留中文、缩进 2。
#       本脚本只动 seeds/ 下的 JSON 源文件；已灌进数据库的历史实例需手动重灌或
#       通过后台「新增」接口补录，脚本本身不会改数据库。
import json
import os

SEEDS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    p = os.path.join(SEEDS_DIR, name)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(name, data):
    p = os.path.join(SEEDS_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _append(name, new_items, title_key="title"):
    data = _load(name)
    existing = {d.get(title_key) for d in data}
    added = 0
    for it in new_items:
        t = it.get(title_key)
        if t and t not in existing:
            data.append(it)
            existing.add(t)
            added += 1
    _save(name, data)
    return added


# ==================== 育儿知识文章 ====================
NEW_ARTICLES = [
    {"category": "喂养", "title": "宝宝厌奶期应对", "age_range": "3-6个月",
     "content": "厌奶期多出现在3-6个月，宝宝突然吃奶量减少、吃奶不专心，但精神好、体重增长正常，多为生理性。不要强迫喂奶，可尝试在安静环境喂奶、拉长喂奶间隔、更换喂奶姿势。若伴随发热、精神差、体重不增需排查疾病因素。"},
    {"category": "喂养", "title": "母乳的储存与解冻", "age_range": "0-12个月",
     "content": "挤出的母乳室温（25°C以下）可存放4小时，冷藏（4°C）可放3-5天，冷冻（-18°C）可存3-6个月。解冻用温水解冻或冷藏室提前转移，不要用微波炉。解冻后室温放置不超过1-2小时，未吃完的应丢弃，不可再次冷冻。"},
    {"category": "喂养", "title": "夜奶的科学断离", "age_range": "6-12个月",
     "content": "6个月后宝宝胃容量增大、辅食添加，夜奶需求逐渐降低。断夜奶先从减少次数和奶量开始，用轻拍、安抚巾替代喂奶哄睡，循序渐进。出牙期、生病或猛长期可暂缓。断夜奶有助于宝宝和妈妈睡眠质量，但不必与断奶混为一谈。"},
    {"category": "健康", "title": "中耳炎识别与护理", "age_range": "0-3岁",
     "content": "婴幼儿中耳炎的常见表现：夜间耳痛哭闹、频繁抓耳、发热、耳朵流脓、对声音反应变差。感冒后易继发。不要自行用滴耳液，应保持鼻腔通畅、避免平躺喂奶。一旦怀疑中耳炎应尽早就医，按医嘱用抗生素，防止听力受损。"},
    {"category": "健康", "title": "尿路感染防范", "age_range": "0-3岁",
     "content": "女婴因尿道短更易发生尿路感染，表现为发热、排尿哭闹、尿液浑浊或异味、拒食。护理要点：从前向后擦拭、勤换尿布、不长时间穿纸尿裤闷着。反复发热又找不到原因时，应查尿常规排除尿路感染。"},
    {"category": "健康", "title": "疱疹性咽峡炎", "age_range": "1-6岁",
     "content": "由肠道病毒引起，表现为突发高热、咽部疱疹和溃疡、流涎、拒食，多见于夏秋季。为自限性，重点是退热、补液、缓解咽痛（温凉流质）。注意与手足口病鉴别——本病疱疹只在咽部。隔离休息，通常1周左右恢复。"},
    {"category": "健康", "title": "腺样体肥大早知道", "age_range": "2-6岁",
     "content": "腺样体肥大常见于2-6岁，表现长期张口呼吸、打鼾、睡不踏实、白天注意力不集中，久了可能影响面容（腺样体面容）和听力。若宝宝持续打鼾、张口呼吸超过数月，应到耳鼻喉科评估，必要时药物或手术治疗。"},
    {"category": "健康", "title": "宝宝近视防控", "age_range": "1-6岁",
     "content": "0-3岁尽量不接触电子屏幕，3-6岁每次不超过20分钟。每天保证2小时以上户外活动是最有效的近视预防手段。保证充足睡眠与均衡饮食，定期做视力筛查。发现眯眼、凑近看东西应尽早就诊。"},
    {"category": "发育", "title": "独站与独走训练", "age_range": "9-15个月",
     "content": "多数宝宝9-12个月能扶站，12-15个月独走。引导方法：提供稳固家具练扶站、蹲下站起练习腿力、赤足在安全地面走、用推拉玩具助步。避免过早用学步车。每个宝宝节奏不同，提前或延后1-2个月都属正常。"},
    {"category": "发育", "title": "如厕训练怎么开始", "age_range": "18-36个月",
     "content": "如厕训练宜在18-24个月后、宝宝能表达便意且能短暂憋尿时开始。准备小马桶，固定时间引导坐盆，成功后及时表扬。不要强迫或责骂，尿裤子是学习过程。夜间控尿通常更晚，可顺其自然。"},
    {"category": "睡眠", "title": "新生儿昼夜颠倒调整", "age_range": "0-3个月",
     "content": "新生儿容易昼夜颠倒、白天睡晚上闹。调整方法：白天保持明亮、轻声互动；夜间调暗、少刺激、喂奶换尿布动作轻柔。白天适当唤醒喂奶，逐步拉长夜间睡眠间隔。一般数周后可建立昼夜节律。"},
    {"category": "护理", "title": "宝宝防晒要点", "age_range": "0-3岁",
     "content": "6个月以下以物理遮挡为主（遮阳帽、婴儿车凉棚、避免10-16点外出）；6个月以上可使用婴幼儿专用物理防晒霜，外出前15-20分钟涂抹，出汗后补涂。眼睛也要防护，可戴UV400婴儿墨镜。"},
    {"category": "护理", "title": "防蚊与蚊虫叮咬处理", "age_range": "0-3岁",
     "content": "优先物理防蚊：纱窗、蚊帐、浅色长袖衣裤、电蚊拍。2个月以上可用避蚊胺（DEET）浓度10%以内的驱蚊液，避开眼口手。被叮后冷敷止痒，炉甘石洗剂可用；出现红肿明显或过敏应及时就医。"},
    {"category": "营养", "title": "益生菌该怎么吃", "age_range": "0-3岁",
     "content": "益生菌对抗生素相关腹泻、肠绞痛等有一定帮助，但不是包治百病的补品。应选择有菌株号的正规产品，按说明冷藏保存、温水冲服（不超过40°C）。腹泻、使用抗生素期间可短期补充，平时无需长期吃。"},
    {"category": "早教", "title": "屏幕时间管理", "age_range": "0-3岁",
     "content": "世界卫生组织建议：1岁以内不建议任何屏幕时间；1-2岁仅限高质量陪伴观看；2-3岁每天累计不超过1小时。过多屏幕会挤占互动游戏与睡眠，影响语言与注意力发展。用亲子共读、户外玩耍替代电子保姆。"},
    {"category": "心理", "title": "迎接二宝的家庭准备", "age_range": "1-3岁",
     "content": "怀二宝后提前和大宝沟通，让他参与准备婴儿用品，肯定他作为哥哥姐姐的角色。二宝出生后也要保留专属的大宝时光，不因新成员而忽视。大宝出现退行行为（尿裤子、求抱）是正常反应，多安抚少批评。"},
    {"category": "安全", "title": "居家防溺水", "age_range": "0-3岁",
     "content": "溺水是婴幼儿意外伤害的重要死因，哪怕几厘米深的水也危险。浴缸、水桶、马桶盖随手盖好，洗澡时大人视线不离开。带娃游泳或戏水须全程一臂距离看护，不可依赖救生圈。学会识别溺水（无声、直立、挣扎）与心肺复苏。"},
]

# ==================== 健康百科 ====================
NEW_WIKI = [
    {"category": "呼吸", "title": "中耳炎",
     "content": "中耳炎是儿童常见耳部感染，多由感冒、鼻炎蔓延至中耳引起，表现为耳痛、发热、抓耳、听力下降，婴幼儿常表现为烦躁哭闹、睡眠不安。",
     "symptoms": "耳痛、发热、抓耳、耳道流脓、听力下降、夜间哭闹",
     "cause": "细菌或病毒经咽鼓管侵入中耳，常见于感冒、腺样体肥大之后",
     "treatment": "需就医，按医嘱使用抗生素滴耳/口服，疼痛时对症处理，保持鼻腔通畅",
     "prevention": "预防感冒、正确擤鼻、避免平躺喂奶、接种流感疫苗",
     "when_to_see_doctor": "耳朵流脓、持续高热、听力明显下降或哭闹不止"},
    {"category": "泌尿", "title": "尿路感染",
     "content": "尿路感染在女婴更常见，可累及膀胱或肾脏，表现为发热、排尿哭闹、尿液浑浊异味、拒食，小婴儿症状常不典型。",
     "symptoms": "发热、排尿时哭闹、尿频尿急、尿液浑浊或有异味、拒食",
     "cause": "肠道细菌经尿道上行感染，与卫生、尿布闷湿有关",
     "treatment": "需查尿常规确诊，按医嘱使用抗生素，多喂水促进排尿",
     "prevention": "从前向后擦拭、勤换尿布、不长时间穿闷湿纸尿裤",
     "when_to_see_doctor": "不明原因发热、排尿哭闹或尿液异常"},
    {"category": "呼吸", "title": "疱疹性咽峡炎",
     "content": "由肠道病毒引起，以咽部疱疹和溃疡为特征，突发高热、流涎、拒食，多见于夏秋季，为自限性疾病。",
     "symptoms": "突发高热、咽部疱疹溃疡、流涎、拒食、咽痛",
     "cause": "柯萨奇病毒A组等肠道病毒感染",
     "treatment": "对症退热、补液，温凉流质饮食，保持口腔清洁，通常1周恢复",
     "prevention": "勤洗手、隔离患儿、注意餐具卫生",
     "when_to_see_doctor": "持续高热、脱水、精神极差或不能进食"},
    {"category": "皮肤", "title": "荨麻疹",
     "content": "荨麻疹表现为突发的红色风团、剧烈瘙痒，可数小时内消退又复发，常与过敏、感染、冷热刺激有关。",
     "symptoms": "红色风团、瘙痒、红肿，可伴口唇或眼睑肿胀",
     "cause": "食物、药物、感染、物理刺激等诱发过敏",
     "treatment": "轻症可服抗组胺药缓解，寻找并避开诱因；严重过敏需立即就医",
     "prevention": "识别并避开已知过敏原，感染后及时控制",
     "when_to_see_doctor": "出现呼吸急促、声音嘶哑、口唇肿胀等过敏性休克征兆"},
    {"category": "血液", "title": "缺铁性贫血",
     "content": "缺铁性贫血是婴幼儿最常见的营养性贫血，因铁摄入不足或丢失过多，表现为面色苍白、乏力、食欲差、注意力不集中。",
     "symptoms": "面色苍白、乏力、食欲减退、易疲劳、注意力不集中",
     "cause": "铁储备不足、辅食缺铁、生长过快或慢性失血",
     "treatment": "在医生指导下补充铁剂，同时增加含铁辅食（红肉、肝泥、强化铁米粉）",
     "prevention": "及时添加含铁辅食，早产儿遵医嘱预防性补铁，定期体检",
     "when_to_see_doctor": "面色持续苍白、精神差、生长发育迟缓"},
    {"category": "发育", "title": "小儿斜颈",
     "content": "斜颈表现为头偏向一侧、下巴转向对侧，颈部可摸到梭形包块，多为胸锁乳突肌挛缩所致，早发现早干预效果好。",
     "symptoms": "头偏一侧、颈部包块、转头受限",
     "cause": "胎位、产伤或肌肉发育因素导致胸锁乳突肌挛缩",
     "treatment": "以手法牵拉、姿势矫正为主，严重者需康复或手术",
     "prevention": "日常多引导宝宝向受限侧转头，俯卧时间充足",
     "when_to_see_doctor": "发现头偏、颈部包块或转头明显受限"},
    {"category": "外科", "title": "腹股沟疝",
     "content": "腹股沟疝表现为大腿根部可复性包块，哭闹时明显、安静时缩小，因腹压高致肠管或网膜突出。",
     "symptoms": "腹股沟或阴囊可复性包块，哭闹时增大",
     "cause": "鞘状突未闭，腹内容物经薄弱处突出",
     "treatment": "多需手术治疗（疝囊高位结扎），嵌顿疝为急症",
     "prevention": "减少长时间剧烈哭闹、便秘等腹压增高因素",
     "when_to_see_doctor": "包块突然变大、变硬、疼痛、不能回纳（嵌顿）需立即就医"},
    {"category": "急症", "title": "蒙被综合征",
     "content": "蒙被综合征因过度保暖、捂闷导致宝宝高热、大汗、脱水甚至呼吸衰竭，多见于小婴儿，冬春季高发，十分危险。",
     "symptoms": "高热、大汗、脱水、精神萎靡、呼吸急促",
     "cause": "过度包裹、被褥捂闷、室温过高",
     "treatment": "立即降温、松解包被、补充水分，严重者立即送医",
     "prevention": "睡眠不过度保暖，婴儿床不放枕头毛绒物，室温适宜",
     "when_to_see_doctor": "发现高热大汗、反应差、呼吸异常立即就医"},
    {"category": "呼吸", "title": "腺样体肥大",
     "content": "腺样体肥大常见于2-6岁，因反复炎症增生，阻塞后鼻孔致长期张口呼吸、打鼾，影响睡眠与面容。",
     "symptoms": "长期张口呼吸、打鼾、睡不踏实、听力下降、腺样体面容",
     "cause": "反复上呼吸道感染、过敏等因素刺激腺样体增生",
     "treatment": "轻症药物控制，重度或面容受影响需手术切除",
     "prevention": "预防感冒与过敏，及时治疗鼻炎",
     "when_to_see_doctor": "持续打鼾、张口呼吸数月或听力下降"},
    {"category": "眼睛", "title": "屈光不正与近视",
     "content": "儿童近视多与近距离用眼、户外不足有关，表现为看远模糊、眯眼、凑近。早发现可延缓进展。",
     "symptoms": "眯眼、凑近看物、揉眼、注意力不集中",
     "cause": "遗传、长时间近距离用眼、缺乏户外光照",
     "treatment": "医学验光配镜，必要时防控手段（如户外、药物或光学干预）",
     "prevention": "每天2小时户外、控制屏幕、充足睡眠",
     "when_to_see_doctor": "发现眯眼、凑近看东西或幼儿园视力筛查异常"},
    {"category": "外科", "title": "包茎与包皮过长",
     "content": "多数男婴为生理性包茎，随年龄可自然缓解。若无反复炎症、排尿困难，多不需过早处理。",
     "symptoms": "包皮口狭小、排尿鼓包、反复红肿",
     "cause": "先天包皮口窄，部分为后天粘连",
     "treatment": "保持清洁、轻柔上翻清洗；反复感染或严重狭窄需就医评估",
     "prevention": "日常清洗外阴，不强行上翻包皮",
     "when_to_see_doctor": "排尿困难、包皮反复红肿化脓"},
]

# ==================== 辅食食谱 ====================
NEW_RECIPES = [
    {"title": "小米南瓜粥", "icon": "🥣", "age_group": "7月+", "category": "谷物",
     "ingredients": "小米、南瓜", "instructions": "1. 小米淘洗，南瓜去皮切块\n2. 小米加水煮成软粥\n3. 南瓜蒸熟压泥拌入粥中\n4. 煮匀即可",
     "allergens": "无", "prep_time": "5", "cook_time": "35", "servings": "2",
     "nutrition": "富含β-胡萝卜素和B族维生素和膳食纤维", "tips": "南瓜自带甜味，宝宝接受度高。"},
    {"title": "山药小米粥", "icon": "🥣", "age_group": "7月+", "category": "谷物",
     "ingredients": "铁棍山药、小米", "instructions": "1. 山药去皮切小块（戴手套）\n2. 小米煮成粥\n3. 山药蒸熟压泥加入\n4. 煮5分钟搅匀",
     "allergens": "无", "prep_time": "10", "cook_time": "35", "servings": "2",
     "nutrition": "健脾养胃，易消化", "tips": "山药黏液蛋白对肠胃友好。"},
    {"title": "青菜香菇面", "icon": "🍜", "age_group": "12月+", "category": "混合",
     "ingredients": "宝宝面条、青菜、香菇", "instructions": "1. 香菇泡发切碎，青菜焯水切碎\n2. 面条煮软切小段\n3. 清水煮开下面与香菇\n4. 加青菜再煮2分钟",
     "allergens": "小麦", "prep_time": "10", "cook_time": "15", "servings": "2",
     "nutrition": "富含膳食纤维和维生素", "tips": "香菇提鲜，少盐更利口。"},
    {"title": "蒸红薯条", "icon": "🍠", "age_group": "8月+", "category": "手指食物",
     "ingredients": "红薯", "instructions": "1. 红薯洗净去皮切粗条\n2. 蒸15-20分钟至软烂\n3. 放温后让宝宝抓食",
     "allergens": "无", "prep_time": "10", "cook_time": "20", "servings": "1",
     "nutrition": "富含膳食纤维和β-胡萝卜素", "tips": "条状方便抓握，锻炼自主进食。"},
    {"title": "蒸南瓜条", "icon": "🎃", "age_group": "8月+", "category": "手指食物",
     "ingredients": "南瓜", "instructions": "1. 南瓜去皮切粗条\n2. 蒸10-15分钟至软\n3. 温后让宝宝抓食",
     "allergens": "无", "prep_time": "8", "cook_time": "15", "servings": "1",
     "nutrition": "富含β-胡萝卜素", "tips": "软硬适中，适合练习抓握。"},
    {"title": "西兰花蒸糕", "icon": "🥦", "age_group": "10月+", "category": "手指食物",
     "ingredients": "西兰花、鸡蛋、面粉", "instructions": "1. 西兰花焯水切碎\n2. 鸡蛋打散加少许面粉\n3. 拌入西兰花碎\n4. 上锅蒸10分钟切块",
     "allergens": "鸡蛋、小麦", "prep_time": "10", "cook_time": "10", "servings": "1",
     "nutrition": "富含维生素C和优质蛋白", "tips": "软糯好抓，锻炼手口协调。"},
    {"title": "虾泥蒸蛋", "icon": "🦐", "age_group": "10月+", "category": "蛋白质",
     "ingredients": "鲜虾、鸡蛋", "instructions": "1. 鲜虾去壳去虾线切小粒\n2. 鸡蛋打散加1.5倍温水\n3. 虾粒放入蛋液\n4. 盖膜蒸10分钟",
     "allergens": "虾、鸡蛋", "prep_time": "10", "cook_time": "10", "servings": "1",
     "nutrition": "富含钙和优质蛋白", "tips": "虾粒要小，首次少量试过敏。"},
    {"title": "银鱼蒸蛋", "icon": "🐟", "age_group": "10月+", "category": "蛋白质",
     "ingredients": "银鱼、鸡蛋", "instructions": "1. 银鱼洗净沥干\n2. 鸡蛋加温水打散\n3. 银鱼拌入蛋液\n4. 蒸8-10分钟",
     "allergens": "鸡蛋、鱼类", "prep_time": "8", "cook_time": "10", "servings": "1",
     "nutrition": "富含钙和DHA", "tips": "银鱼刺少，补钙佳品。"},
    {"title": "冬瓜肉丸汤", "icon": "🍲", "age_group": "12月+", "category": "汤羹",
     "ingredients": "冬瓜、猪里脊、淀粉", "instructions": "1. 猪肉剁泥加淀粉葱姜水拌匀\n2. 冬瓜去皮切片\n3. 水开下冬瓜，再捏小丸子入锅\n4. 煮5分钟至丸子浮起",
     "allergens": "无", "prep_time": "15", "cook_time": "15", "servings": "2",
     "nutrition": "清淡补水、优质蛋白", "tips": "丸子搓小些，方便吞咽。"},
    {"title": "萝卜排骨汤（宝宝版）", "icon": "🍲", "age_group": "18月+", "category": "汤羹",
     "ingredients": "白萝卜、排骨", "instructions": "1. 排骨焯水去血沫\n2. 白萝卜切块\n3. 同炖1小时至萝卜软烂\n4. 撇去浮油，取汤和软萝卜",
     "allergens": "无", "prep_time": "15", "cook_time": "60", "servings": "3",
     "nutrition": "补钙、润肺", "tips": "去浮油减腻，汤鲜萝卜甜。"},
    {"title": "红豆沙", "icon": "🫘", "age_group": "12月+", "category": "甜品",
     "ingredients": "红豆、冰糖", "instructions": "1. 红豆泡2小时\n2. 加水煮软烂\n3. 加少许冰糖\n4. 压成沙或保留颗粒",
     "allergens": "无", "prep_time": "10", "cook_time": "40", "servings": "2",
     "nutrition": "富含铁和膳食纤维", "tips": "1岁以上可少量加糖，不过甜。"},
    {"title": "蔬菜鸡蛋卷", "icon": "🥞", "age_group": "12月+", "category": "混合",
     "ingredients": "鸡蛋、胡萝卜、菠菜、面粉", "instructions": "1. 蔬菜切碎焯水\n2. 鸡蛋加少许面粉打散拌蔬菜\n3. 小火摊成薄饼卷起\n4. 切小段",
     "allergens": "鸡蛋、小麦", "prep_time": "10", "cook_time": "10", "servings": "1",
     "nutrition": "蛋白质与维生素均衡", "tips": "薄饼易卷，色彩丰富。"},
    {"title": "时蔬炒饭", "icon": "🍚", "age_group": "18月+", "category": "混合",
     "ingredients": "米饭、鸡蛋、豌豆、玉米、胡萝卜", "instructions": "1. 蔬菜切小丁焯水\n2. 鸡蛋炒散\n3. 下蔬菜与米饭翻炒均匀\n4. 少油炒香",
     "allergens": "鸡蛋", "prep_time": "10", "cook_time": "10", "servings": "2",
     "nutrition": "碳水与多种维生素", "tips": "米饭用隔夜冷藏的更松散。"},
    {"title": "鸡肉蔬菜饼", "icon": "🍗", "age_group": "12月+", "category": "肉类",
     "ingredients": "鸡胸肉、胡萝卜、西兰花", "instructions": "1. 鸡肉蔬菜搅碎拌匀\n2. 团成小饼\n3. 小火少油煎熟\n4. 切条方便抓",
     "allergens": "无", "prep_time": "15", "cook_time": "12", "servings": "4",
     "nutrition": "优质蛋白与蔬菜纤维", "tips": "煎至金黄，外香里嫩。"},
    {"title": "番茄龙利鱼粥", "icon": "🐟", "age_group": "12月+", "category": "混合",
     "ingredients": "龙利鱼、番茄、大米", "instructions": "1. 大米煮成粥\n2. 番茄去皮炒出汁\n3. 鱼蒸熟拆碎无刺拌入\n4. 煮3分钟",
     "allergens": "鱼类", "prep_time": "15", "cook_time": "40", "servings": "2",
     "nutrition": "富含DHA和番茄红素", "tips": "龙利鱼刺少，务必拆净。"},
    {"title": "红枣山药羹", "icon": "🍲", "age_group": "10月+", "category": "汤羹",
     "ingredients": "红枣、山药", "instructions": "1. 红枣去核，山药去皮切块\n2. 同煮软烂\n3. 打成羹或压泥\n4. 温后喂食",
     "allergens": "无", "prep_time": "10", "cook_time": "30", "servings": "2",
     "nutrition": "健脾、补铁，天然甜味", "tips": "红枣去核防噎，少量即可。"},
]

# ==================== 活动游戏 ====================
NEW_ACTIVITIES = [
    {"title": "扶站练习", "icon": "🧍", "category": "physical", "min_age_months": 8, "max_age_months": 14,
     "summary": "让宝宝扶着沙发或围栏站立，锻炼腿部力量与平衡。", "duration": "10分钟",
     "materials": "稳固沙发、围栏", "steps": "1. 扶宝宝双手让其站稳\n2. 引导扶家具独自站\n3. 用玩具在前方吸引\n4. 每次几分钟，逐渐延长",
     "benefits": "腿部力量、平衡感、为独走准备", "difficulty": "简单", "tags": "扶站,腿部力量,平衡"},
    {"title": "认识身体部位", "icon": "👶", "category": "language", "min_age_months": 12, "max_age_months": 36,
     "summary": "边指边说「这是眼睛、这是鼻子」，教宝宝认识身体部位。", "duration": "10分钟",
     "materials": "无", "steps": "1. 指着自己五官说名称\n2. 轻点宝宝对应部位\n3. 问「鼻子在哪里」引导指\n4. 做对即鼓掌",
     "benefits": "语言发展、身体认知、亲子互动", "difficulty": "简单", "tags": "身体认知,语言,认知"},
    {"title": "分享练习", "icon": "🤝", "category": "social", "min_age_months": 18, "max_age_months": 36,
     "summary": "通过互换玩具引导宝宝体验分享与轮流。", "duration": "10分钟",
     "materials": "两件小玩具", "steps": "1. 家长拿A递给宝宝换B\n2. 说「我们换一换」\n3. 轮流玩并表扬\n4. 不强迫，循序渐进",
     "benefits": "社交技能、轮流意识、情绪", "difficulty": "简单", "tags": "分享,社交,轮流"},
    {"title": "爬行比赛", "icon": "🐛", "category": "physical", "min_age_months": 7, "max_age_months": 14,
     "summary": "家长和宝宝一起爬，或引导宝宝爬向目标，增加爬行乐趣。", "duration": "10分钟",
     "materials": "软垫、玩具", "steps": "1. 铺好软垫\n2. 家长在前方示范爬\n3. 用玩具引导宝宝追\n4. 到达即鼓励",
     "benefits": "四肢协调、大运动、亲子互动", "difficulty": "简单", "tags": "爬行,大运动,互动"},
    {"title": "橡皮泥感官", "icon": "🎨", "category": "sensory", "min_age_months": 12, "max_age_months": 36,
     "summary": "用安全橡皮泥揉捏，感受柔软与形状变化。", "duration": "15分钟",
     "materials": "安全橡皮泥", "steps": "1. 给宝宝一团泥\n2. 示范揉、搓、压\n3. 用模具压形\n4. 自由创作",
     "benefits": "触觉发展、精细动作、创造力", "difficulty": "简单", "tags": "橡皮泥,感官,精细"},
    {"title": "沙锤演奏", "icon": "🥁", "category": "music", "min_age_months": 6, "max_age_months": 24,
     "summary": "用自制沙锤随音乐打节奏，培养节奏感。", "duration": "10分钟",
     "materials": "空瓶、豆子（封口）、儿歌", "steps": "1. 装入少量豆子封口\n2. 播放儿歌\n3. 和宝宝一起摇\n4. 变换快慢",
     "benefits": "节奏感、听觉、亲子互动", "difficulty": "简单", "tags": "沙锤,音乐,节奏"},
    {"title": "看图说话", "icon": "📖", "category": "language", "min_age_months": 18, "max_age_months": 36,
     "summary": "指着绘本画面引导宝宝说出看到的物品名称。", "duration": "15分钟",
     "materials": "认知绘本", "steps": "1. 选图大字少的绘本\n2. 指图问「这是什么」\n3. 宝宝说不出时示范\n4. 鼓励模仿",
     "benefits": "语言表达、认知、专注力", "difficulty": "简单", "tags": "绘本,语言,表达"},
    {"title": "浮沉实验", "icon": "💧", "category": "cognitive", "min_age_months": 18, "max_age_months": 36,
     "summary": "把不同物品放入水中，观察哪些浮起哪些沉下。", "duration": "15分钟",
     "materials": "水盆、塑料瓶、小石头、海绵", "steps": "1. 准备水和若干物品\n2. 逐一放入看浮沉\n3. 用语言描述\n4. 引导猜测再验证",
     "benefits": "科学探索、因果认知、观察力", "difficulty": "中等", "tags": "浮沉,科学,探索"},
    {"title": "手指画", "icon": "🖍️", "category": "creative", "min_age_months": 12, "max_age_months": 36,
     "summary": "用可食用颜料或酸奶在纸上用手指作画。", "duration": "15分钟",
     "materials": "可食用颜料、大纸", "steps": "1. 铺好纸\n2. 蘸取安全颜料\n3. 自由涂抹\n4. 不强调像什么",
     "benefits": "创造力、感官、精细动作", "difficulty": "简单", "tags": "手指画,创意,感官"},
    {"title": "穿鞋练习", "icon": "👟", "category": "motor", "min_age_months": 24, "max_age_months": 36,
     "summary": "教宝宝分辨左右、自己穿鞋，培养生活自理。", "duration": "10分钟",
     "materials": "易穿的软底鞋", "steps": "1. 示范分清左右\n2. 帮宝宝把脚伸进鞋\n3. 引导自己提鞋跟\n4. 完成即表扬",
     "benefits": "自理能力、秩序感、精细动作", "difficulty": "中等", "tags": "穿鞋,自理,生活技能"},
    {"title": "平衡车初体验", "icon": "🚲", "category": "physical", "min_age_months": 24, "max_age_months": 36,
     "summary": "在平地用学步平衡车练习蹬地滑行。", "duration": "15分钟",
     "materials": "低龄平衡车、头盔", "steps": "1. 戴好头盔在平地\n2. 示范坐稳蹬地\n3. 扶一下让宝宝滑\n4. 短距离反复",
     "benefits": "平衡感、大运动、自信", "difficulty": "中等", "tags": "平衡车,平衡,大运动"},
    {"title": "情绪卡片", "icon": "😊", "category": "social", "min_age_months": 18, "max_age_months": 36,
     "summary": "用表情卡片教宝宝识别开心、生气、难过等情绪。", "duration": "10分钟",
     "materials": "情绪表情卡片", "steps": "1. 展示一张卡片说情绪名\n2. 问宝宝「现在开心吗」\n3. 模仿表情\n4. 生活中随时指认",
     "benefits": "情绪认知、社交、语言", "difficulty": "简单", "tags": "情绪,认知,社交"},
    {"title": "蔬菜印画", "icon": "🥦", "category": "creative", "min_age_months": 18, "max_age_months": 36,
     "summary": "用切开的蔬菜蘸颜料盖章，观察不同纹理。", "duration": "15分钟",
     "materials": "生菜、青椒、颜料、纸", "steps": "1. 蔬菜横切\n2. 蘸取安全颜料\n3. 在纸上盖章\n4. 观察纹理",
     "benefits": "创造力、观察力、感官", "difficulty": "简单", "tags": "印画,创意,观察"},
    {"title": "模仿动物", "icon": "🐱", "category": "language", "min_age_months": 12, "max_age_months": 30,
     "summary": "学小猫叫、小兔跳，在游戏中练习发音与动作。", "duration": "10分钟",
     "materials": "无", "steps": "1. 说「小猫怎么叫」并示范\n2. 鼓励宝宝模仿\n3. 换动物继续\n4. 配合动作更欢乐",
     "benefits": "发音、模仿、亲子互动", "difficulty": "简单", "tags": "动物,发音,模仿"},
]


def main():
    res = {}
    res["knowledge_articles"] = _append("knowledge_articles.json", NEW_ARTICLES)
    res["baby_wiki"] = _append("baby_wiki.json", NEW_WIKI)
    res["baby_recipes"] = _append("baby_recipes.json", NEW_RECIPES)
    res["baby_activities"] = _append("baby_activities.json", NEW_ACTIVITIES)
    for k, v in res.items():
        print(f"  +{v}  {k}")
    total = sum(res.values())
    print(f"知识库种子共新增 {total} 条")


if __name__ == "__main__":
    main()
