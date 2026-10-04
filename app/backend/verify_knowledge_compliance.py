#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库内容合规门禁：种子里不许出现「建议用药 / 剂量 / 指定检查」。

为什么值得做这道门禁：2026-10-04 清理百科时靠临时脚本扫，结果**漏了两轮** ——
第一轮正则只覆盖 7 个关键词，漏掉「炉甘石洗剂」「含酮康唑的洗剂」
「母乳强化剂」这类药名与产品名；补完再扫，又发现自己新写的内容也有问题。
两次都是「脚本扫完说干净了」，其实没干净。固化成门禁才不会重犯。

判定方式刻意做成**白名单式**：先认定「这句可能建议用药」，再用例外清单排除
正当语境（禁忌信息、病因描述、抗某药副作用、危险源清单、免疫程序）。
反着写（只列禁用词）会产生大量假失败，几次之后就没人看门禁了 ——
假红比漏更贵。

⚠ 本文件若被本机后台清理程序删掉（历史上发生过，见记忆），
   门禁会静默失效。用 `git log --oneline -- app/backend/verify_knowledge_compliance.py`
   确认它还在仓库里；恢复：`git checkout -- app/backend/verify_knowledge_compliance.py`
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

SEEDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seeds")
FILES = ["knowledge_articles.json", "baby_wiki.json",
         "baby_recipes.json", "baby_activities.json"]

# ── 违规模式：命中即判定为「可能建议用药 / 剂量 / 指定检查 / 下诊断」──
RULES = [
    (r'(炉甘石|酮康唑|阿司匹林|布洛芬|对乙酰氨基|蒙脱石|氧化锌|碘伏|红霉素|'
     r'制霉菌素|激素类|鼻喷|滴耳|抗病毒药|抗生素)', "具体药名或药物类别"),
    (r'\d+\s*(mg|ml|IU|克|毫升|毫克)\b', "具体剂量"),
    (r'(每日|每次|一天)\s*(补充|服用|喂)\s*\d', "每日/每次具体用量"),
    (r'(查|做)\s*(血常规|尿常规|大便常规|X光|胸片|CT|超声|骨密度|过敏原)',
     "点名检查项目"),
    (r'(确诊为|即为|诊断为|就是)\s*\S+', "下诊断结论"),
    (r'(?<!不)建议(自行|自行用药|吃药|服药)', "建议自行用药"),
]

# ── 例外：这些语境下出现上述词是正当的，每条都要能答出「为什么不构成建议」──
EXCEPT = [
    r'禁[用止]',                       # 「禁用阿司匹林」——禁忌，方向相反
    r'对抗|相关腹泻|耐药',             # 「对抗生素相关腹泻有帮助」——用途描述
    r'(有关|相关|刺激|导致)',          # 「长期使用抗生素有关」「母体激素刺激」
    r'(危险源|不要|勿|不可|避免|不宜|防止|自行用)',
    r'(月龄|剂次|第.剂|免疫程序)',    # 「12-15月龄第一剂」
    r'(清洁剂|化妆品|药品|药物)',      # 危险源清单里的名词罗列
    r'脐带残端',                      # 新生儿脐带消毒是访视规范里的标准护理操作，非治疗用药
    r'(奶量|母乳|配方奶|奶瓶)',        # 「每天奶量400-600毫升」是喂养量，不是药物剂量
    r'需就医(检查|评估)',              # 「需就医检查血常规」是就医指引，不是替医生开检查单
]
RULE_RE = [re.compile(p) for p, _ in RULES]
EXC_RE = re.compile('|'.join(EXCEPT))


def main() -> int:
    problems: list[str] = []
    total = 0

    for fn in FILES:
        path = os.path.join(SEEDS, fn)
        if not os.path.isfile(path):
            print(f"  [跳过] 文件不存在 {fn}")
            continue
        raw = io.open(path, encoding="utf-8").read()
        # 用转义写法而不是字面量 U+FFFD：字面量看起来就像乱码，
        # 每次扫乱码都会把它自己当成一处，得再解释一遍「这是故意的」
        if chr(0xFFFD) in raw:
            problems.append(f"{fn}: 存在乱码字符 U+FFFD，"
                            f"通常是批量替换时编码在传输层被破坏")
        try:
            items = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{fn}: JSON 解析失败 {e}")
            continue

        for item in items:
            title = item.get("title", "?")
            total += 1
            for field, val in item.items():
                if not isinstance(val, str) or not val.strip():
                    continue
                for rex, label in zip(RULE_RE, RULES):
                    m = rex.search(val)
                    if not m:
                        continue
                    # 取命中所在整句，再看有没有例外语境
                    start = max(0, val.rfind("。", 0, m.start()) + 1)
                    end = val.find("。", m.end())
                    sentence = val[start: end if end > 0 else len(val)]
                    if EXC_RE.search(sentence):
                        continue
                    problems.append(
                        f"{fn} / {title} / {field}：疑似{label} —— "
                        f"「{sentence.strip()[:60]}」")

    print("=== 知识库内容合规 ===")
    print(f"扫描 {len(FILES)} 个种子文件、{total} 条内容")
    if problems:
        print(f"\n发现 {len(problems)} 处需要处理：")
        for p in problems:
            print(f"  ✗ {p}")
        print("\n口径：生活护理操作、发育观察要点、何时就医信号可以写；"
              "药名、剂量、点名检查、下诊断不行。\n"
              "若某处确属正当语境（如禁忌信息），在 EXCEPT 里补规则并写清理由，"
              "不要直接删检查——那样下次误报又要重新分辨一遍。")
        return 1
    print("通过：未发现建议用药、具体剂量或指定检查项目")
    return 0


if __name__ == "__main__":
    sys.exit(main())
