#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
r"""育儿内容门禁：内容质量体检 + 增量装载验证。

两件事合在一个脚本里，是因为它们是一件事的两面：
内容是给家长看的，写得再对、老用户看不到也等于没有。

A. 内容质量（扫 seeds/*.json，纯静态）
   - 标题重复（同名会让列表出现两行一模一样的，家长以为是自己记错了）
   - 必填字段为空
   - 活动月龄区间合法（min <= max，且落在 0-36 之内）
   - 0-36 月龄是否每个年龄都有活动可做（空洞会让某些月龄的家长打开是一片空）
   - 正文长度下限（太短的条目没有信息量）

B. 增量装载（真起库跑）
   - 新装库：内容全部灌进去
   - 老库补新内容：模拟「库里已有旧内容 + 又新增了几条」⇒ 只补新增的那几条
   - 用户自己删掉的条目不会被重新塞回来
   - 重复跑不产生重复行

跑法：
  C:\Users\X\.workbuddy\binaries\python\envs\babycare\Scripts\python.exe \
      .workbuddy/scripts/verify_content_seed.py
退出码：0 = 通过；1 = 有问题。
"""
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, "app", "backend")
SEEDS = os.path.join(BACKEND, "seeds")
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_content_")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

# 各类内容要求填齐的字段
REQUIRED = {
    "knowledge_articles.json": ("category", "title", "content"),
    "baby_wiki.json": ("category", "title", "content", "when_to_see_doctor"),
    "baby_recipes.json": ("title", "age_group", "category", "ingredients", "instructions"),
    "baby_activities.json": ("title", "category", "min_age_months", "max_age_months", "steps"),
}
MIN_CONTENT_LEN = {
    "knowledge_articles.json": ("content", 40),
    "baby_wiki.json": ("content", 30),
    "baby_recipes.json": ("instructions", 10),
    "baby_activities.json": ("steps", 10),
}

failures = []


def fail(msg):
    failures.append(msg)
    print(f"  [FAIL] {msg}")


def ok(msg):
    print(f"  [OK]   {msg}")


def load(name):
    with open(os.path.join(SEEDS, name), encoding="utf-8") as f:
        return json.load(f)


def check_quality():
    print("=" * 74)
    print("内容质量体检（seeds/*.json）")
    print("=" * 74)

    for name, fields in REQUIRED.items():
        rows = load(name)
        print(f"\n{name}：{len(rows)} 条")

        # 1) 标题重复
        seen = {}
        dup = []
        for r in rows:
            t = (r.get("title") or "").strip()
            if not t:
                fail(f"{name} 有条目没有标题")
                continue
            if t in seen:
                dup.append(t)
            seen[t] = 1
        if dup:
            fail(f"{name} 标题重复 {len(set(dup))} 个：{sorted(set(dup))[:5]}")
        else:
            ok("无重复标题")

        # 2) 必填字段为空
        holes = []
        for i, r in enumerate(rows):
            for f in fields:
                v = r.get(f)
                if v is None or (isinstance(v, str) and not v.strip()):
                    holes.append(f"第{i+1}条({r.get('title','?')})缺 {f}")
        if holes:
            fail(f"{name} 必填字段为空 {len(holes)} 处：{holes[:3]}")
        else:
            ok("必填字段齐全")

        # 3) 正文长度下限
        field, minlen = MIN_CONTENT_LEN[name]
        short = [r.get("title") for r in rows if len(str(r.get(field) or "")) < minlen]
        if short:
            fail(f"{name} 正文过短（{field} < {minlen} 字）{len(short)} 条：{short[:3]}")
        else:
            ok(f"正文长度均 ≥ {minlen} 字")

    # 4) 活动月龄区间与覆盖
    acts = load("baby_activities.json")
    bad_range = [a.get("title") for a in acts
                 if not (0 <= int(a.get("min_age_months", 0)) <= int(a.get("max_age_months", 0)) <= 36)]
    if bad_range:
        fail(f"活动月龄区间非法：{bad_range[:5]}")
    else:
        ok("活动月龄区间合法（0 ≤ min ≤ max ≤ 36）")

    cover = [0] * 37
    for a in acts:
        for m in range(int(a.get("min_age_months", 0)), int(a.get("max_age_months", 0)) + 1):
            if 0 <= m <= 36:
                cover[m] += 1
    gap = [m for m in range(0, 37) if cover[m] == 0]
    thin = [m for m in range(0, 37) if 0 < cover[m] < 3]
    if gap:
        fail(f"这些月龄没有任何活动可做：{gap}")
    elif thin:
        fail(f"这些月龄可选活动不足 3 个：{thin}")
    else:
        ok("0-36 月龄每个月都有 ≥3 个活动")

    # 5) 月龄指导与 guidance_data 的分档要对得上（两处口径不一致会出现
    #    「本月要点」写着婴儿晚期、发育特点却按婴儿早期给）
    import guidance_data
    from blueprints import knowledge
    expect = [3, 6, 12, 18, 24]
    got = []
    for m in range(0, 40):
        key = guidance_data.stage_of(m)
        if not got or got[-1] != key:
            got.append(key)
    if len(got) != len(expect) + 1:
        fail(f"guidance_data 分档数量不对：{got}")
    else:
        ok(f"guidance_data 分档：{' → '.join(got)}")

    # 与 knowledge.py 的 _get_parent_guide_by_age 核对同一月龄给的 stage 名一致
    mismatch = []
    for m in (0, 3, 4, 6, 7, 12, 13, 18, 19, 24, 25, 36):
        a = knowledge._get_parent_guide_by_age(m).get("stage")
        b = guidance_data.stage_label(m)
        if a != b:
            mismatch.append(f"{m}月龄：家长指导={a} / guidance_data={b}")
    if mismatch:
        fail("两处分档口径不一致：" + "；".join(mismatch[:3]))
    else:
        ok("家长指导与 guidance_data 的月龄分档一致")


def check_incremental_seed():
    print("\n" + "=" * 74)
    print("增量装载验证（真起库）")
    print("=" * 74)

    import server
    from seed_data import load_all_seeds

    # init_db() 只建表；种子是启动流程里单独一步（server._do_init），这里照做
    server.init_db()
    load_all_seeds()
    db = os.environ["BABYCARE_DB_PATH"]
    con = sqlite3.connect(db)

    def count(table):
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def titles(table):
        return {r[0] for r in con.execute(f"SELECT title FROM {table}")}

    before = count("knowledge_articles")
    if before == 0:
        fail("新装库没灌进任何知识文章")
        return
    ok(f"新装库灌入知识文章 {before} 条 / 百科 {count('baby_wiki')} 条 / "
       f"食谱 {count('baby_recipes')} 条 / 活动 {count('baby_activities')} 条")

    # ① 用户删掉一条 ⇒ 再次装载不该把它塞回来
    victim = sorted(titles("knowledge_articles"))[0]
    con.execute("DELETE FROM knowledge_articles WHERE title = ?", (victim,))
    con.commit()
    load_all_seeds()
    if victim in titles("knowledge_articles"):
        fail(f"用户删掉的《{victim}》被重新塞回来了")
    else:
        ok(f"用户删掉的《{victim}》不会被重新灌入")
    con.execute(
        "INSERT INTO knowledge_articles (category, title, age_range, content) VALUES (?,?,?,?)",
        ("测试", victim, "0-3岁", "用于还原的占位内容"))
    con.commit()

    # ② 新增内容 ⇒ 老库要能补上（模拟「以后又写了新文章」）
    src = os.path.join(SEEDS, "knowledge_articles.json")
    original = open(src, encoding="utf-8").read()
    data = json.loads(original)
    marker_title = "ZZ增量装载测试条目"
    data.append({"category": "测试", "title": marker_title,
                 "age_range": "0-3岁", "content": "这是一条用于验证增量装载的临时条目。" * 5})
    try:
        with open(src, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        base = count("knowledge_articles")
        load_all_seeds()
        after = count("knowledge_articles")
        if marker_title not in titles("knowledge_articles"):
            fail("新增的内容没有补进已有库（老用户看不到新内容）")
        elif after != base + 1:
            fail(f"增量装载数量不对：{base} → {after}（应只 +1）")
        else:
            ok(f"新增 1 条内容能补进已有库（{base} → {after}）")

        # ③ 重复跑不产生重复
        load_all_seeds()
        again = count("knowledge_articles")
        if again != after:
            fail(f"重复装载产生了重复行：{after} → {again}")
        else:
            ok("重复装载幂等，不产生重复行")
    finally:
        with open(src, "w", encoding="utf-8") as f:
            f.write(original)
        con.execute("DELETE FROM knowledge_articles WHERE title = ?", (marker_title,))
        con.commit()
    con.close()


def main():
    check_quality()
    check_incremental_seed()
    print("\n" + "=" * 74)
    if failures:
        print(f"共 {len(failures)} 项不通过：")
        for f in failures:
            print("  - " + f)
        return 1
    print("全部通过：内容质量达标，且新内容能到达老用户")
    return 0


if __name__ == "__main__":
    sys.exit(main())
