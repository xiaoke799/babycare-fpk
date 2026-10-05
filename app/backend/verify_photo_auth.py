#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""照片链路鉴权回归门禁（临时库，不碰正式数据）。

起因（2026-10-06 媒体链路打磨）：
    逐Tab 体检健康档案时顺手看了 photos.py，发现**同一资源的两端权限不一致**：
      DELETE /api/photos/<id> 挂了 @require_admin（判身份 is_admin）
      PUT    /api/photos/<id> **一个守卫都没有**
    全局 before_request 只采信网关 X-Trim-* 头（且仅 gateway_trusted 时），
    并不做登录校验 ⇒ 未登录请求打 PUT 能改照片元数据。
    这是两个方向相反的毛病：删除过紧（家庭非管理员成员删不了自家照片）、
    修改过松（谁都能改）。

本门禁钉住三条不变量：
  A. 未登录 PUT/DELETE 一律401（不许"没登录也能改/删"）
  B. 已登录非管理员 **可以** 改/删自家照片（require_admin 是判身份，过紧了）
  C. baby_id 归属校验：改A 宝宝的照片不能影响 B 宝宝的封面唯一性

⚠ 判据纪律：不写「不是 500就算过」——越界/未授权天然可能不报错，
  要设计成**「钳制生效时结果会变」**：真的 401 /真的 200 且数据真变了。
"""
import os
import sys
import sqlite3
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_photo_auth_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")
os.environ["BABYCARE_DEV_AUTH"] = "1"

_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402
from flask import g  # noqa: E402

_passed = 0
_failed = 0
FAILS = []


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  [OK]   %s" % name)
    else:
        _failed += 1
        FAILS.append(name)
        print("  [FAIL] %s%s" % (name, ("<-- " + str(extra)) if extra else ""))


def seed():
    """重建两个宝宝各一张照片，返回 (甲的照片 id, 乙的照片 id)。
    ⚠ 列名按真实 schema 写：babies 是 **birthday**（不是 birth_date），
    photos **没有 category 列**。早先照着别处的字段名猜，直接
    OperationalError 崩掉——先 PRAGMA table_info 读一遍再写。
    🔴 必须回读真实 id、不能硬编码 1/2：DELETE 不会重置 AUTOINCREMENT 序列，
    第二次 seed 拿到的 id 是 3/4，而断言还按 1/2 打⇒ 404。
    **这是测试写错，不是代码问题**，第一版就栽在这，差点去"修"后端。
    """
    conn = sqlite3.connect(TMP_DB)
    try:
        conn.execute("DELETE FROM photos")
        conn.execute("DELETE FROM babies")
        conn.execute(
            "INSERT INTO babies (name, birthday, gender) VALUES ('甲', '2025-01-01', 'boy')"
        )
        conn.execute(
            "INSERT INTO babies (name, birthday, gender) VALUES ('乙', '2025-02-01', 'girl')"
        )
        a_id = conn.execute(
            "INSERT INTO photos (baby_id, photo_date, description, file_path, is_cover)"
            " SELECT id, '2025-06-01', '甲的照片', 'photos/a.jpg', 0 FROM babies WHERE name='甲'"
        ).lastrowid
        b_id = conn.execute(
            "INSERT INTO photos (baby_id, photo_date, description, file_path, is_cover)"
            " SELECT id, '2025-06-01', '乙的照片', 'photos/b.jpg', 0 FROM babies WHERE name='乙'"
        ).lastrowid
        conn.commit()
        # 回读真实 baby_id：断言别写死 1/2（seed 重建后自增 id 会变）
        a_baby = conn.execute("SELECT id FROM babies WHERE name='甲'").fetchone()[0]
        b_baby = conn.execute("SELECT id FROM babies WHERE name='乙'").fetchone()[0]
        return a_id, b_id, a_baby, b_baby
    finally:
        conn.close()


def logout(c):
    with c.session_transaction() as s:
        s.pop("authenticated", None)
        s.clear()


def login_plain(c):
    """种一个「已登录但非管理员」的会话。"""
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["user_id"] = "family-member"


def q1(sql, args=()):
    """直连 sqlite3 读一个值。
    🔴 不用 server.get_db()：它走 flask.g，脱离 app context 会 RuntimeError，
    在 context 外调用时整个脚本崩掉——「脚本崩了」和「门禁全过」长得一样。
    """
    conn = sqlite3.connect(TMP_DB)
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def qall(sql, args=()):
    conn = sqlite3.connect(TMP_DB)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def main():
    print("=" * 72)
    print("照片链路鉴权门禁 · 未登录 / 非管理员 / 跨宝宝")
    print("=" * 72)

    app = server.app
    app.config["TESTING"] = True
    c = app.test_client()

    # ⚠ 必须先 init_db() 建表：不建的话 seed() 报「no such table: photos」，
    #   而"脚本崩了"和"门禁全过"在输出上很像（项目铁律：0 条失败先怀疑脚本崩了）。
    server.init_db()

    # 🔴 get_db() 走 flask.g，必须有 app context；早先在 context 外调用
    #    直接 RuntimeError 崩掉——「脚本崩了」长得和「门禁全过」一样，
    #    差点被误读成没问题（项目铁律：0 条失败先怀疑脚本自己崩了）。
    photo_a, photo_b, baby_a, baby_b = seed()

    # ---------- A. 未登录 ----------
    print("A. 未登录访问")
    logout(c)
    r = c.put("/api/photos/%d" % photo_a, json={"description": "被改了"})
    check("未登录 PUT 被拒(401/403)", r.status_code in (401, 403), "实际 %s" % r.status_code)
    r = c.delete("/api/photos/%d" % photo_a)
    check("未登录 DELETE 被拒(401/403)", r.status_code in (401, 403), "实际 %s" % r.status_code)

    db = None  # 断言一律走 q1/qall（直连 sqlite3）
    d = q1("SELECT description FROM photos WHERE id=?", (photo_a,))
    check("未登录 PUT 没有真的改到数据", d == "甲的照片", d)
    check("照片还在(没被删)", q1("SELECT COUNT(*) FROM photos WHERE id=?", (photo_a,)) == 1)

    # ---------- B. 已登录非管理员 ----------
    print()
    print("B. 已登录非管理员（家庭成员，is_admin=False）")
    login_plain(c)
    r = c.put("/api/photos/%d" % photo_a, json={"description": "成员改的"})
    check("非管理员 PUT 允许(200)", r.status_code == 200, "实际 %s" % r.status_code)
    d = q1("SELECT description FROM photos WHERE id=?", (photo_a,))
    check("非管理员 PUT 的修改真的落库", d == "成员改的", d)

    r = c.delete("/api/photos/%d" % photo_b)
    check("非管理员 DELETE 允许(200/204)", r.status_code in (200, 204),
          "实际 %s —— require_admin 判的是身份，误把家庭成员当外人" % r.status_code)

    # ---------- C. 封面唯一性（**每个宝宝各自一张**） ----------
    print()
    print("C. 封面唯一性")
    # ⚠ 必须重新 seed 并接住新的 id：B 组刚把 photo_b 删掉了。
    #   第一版没接返回值、还硬编码 1/2 ⇒ 404，看着像"设封面功能坏了"。
    photo_a, photo_b, baby_a, baby_b = seed()
    login_plain(c)
    c.put("/api/photos/%d" % photo_a, json={"is_cover": True})
    r = c.put("/api/photos/%d" % photo_b, json={"is_cover": True})
    check("把乙设为封面成功", r.status_code == 200, "实际 %s" % r.status_code)
    covers = sorted(row[0] for row in qall("SELECT id FROM photos WHERE is_cover=1"))
    # ⚠ 判据一度写成"封面全局唯一"，结果报 [3,4] 两张同时是封面。
    #   查photos.py:442 的文档注释才看清：**设计就是每个宝宝各自一张封面**
    #   （清零语句 `WHERE baby_id = ?` 是对的）。判据写错会逼我去改正确的
    #   产品逻辑——这正是「新写判据先问这条错判会误伤谁」那条铁律。
    check("甲的封面仍然有效（各自一张，不是全局唯一）", photo_a in covers, covers)
    check("新设的乙成为封面", photo_b in covers, covers)
    check("甲宝宝下只有一张封面", q1(
        "SELECT COUNT(*) FROM photos WHERE is_cover=1 AND baby_id=?", (baby_a,)) == 1)
    check("乙宝宝下只有一张封面", q1(
        "SELECT COUNT(*) FROM photos WHERE is_cover=1 AND baby_id=?", (baby_b,)) == 1)

    # ---------- D. 不存在的 id ----------
    print()
    print("D. 越界 id")
    r = c.put("/api/photos/999999", json={"description": "x"})
    check("改不存在的照片返回 404", r.status_code == 404, "实际 %s" % r.status_code)
    r = c.delete("/api/photos/999999")
    check("删不存在的照片返回 404", r.status_code == 404, "实际 %s" % r.status_code)

    print()
    print("=" * 72)
    if _failed:
        print("结果：%d 过 / %d 挂" % (_passed, _failed))
        for f in FAILS:
            print("  挂：%s" % f)
        return 1
    print("结果：%d 过 / 0 挂" % _passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
