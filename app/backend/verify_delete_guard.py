#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
删除接口「假成功」守卫体检：不启服务、不碰真实数据库。

背景（2026-09-19 实测）：
  一批 DELETE 处理函数执行完 `DELETE FROM t WHERE id = ?` 后**不检查 rowcount**，
  直接 `return jsonify({"success": True, "message": "已删除"})`。
  后果是「记录不存在 / 不属于该宝宝 / 已被删过」时，接口仍回 200 + success=True，
  前端弹「已删除」但库里什么都没变 —— 典型的静默缺陷（不报错、不写日志）。

  同项目其它删除（喂奶 / 睡眠 / 尿布 / 照片 / 知识库）都做了 rowcount 或先 SELECT 判定，
  说明这是**遗漏**而不是「幂等删除」的设计意图。

本脚本对每个「按 id 删单条」的接口都发一次**不存在的 id**，断言回 404。
批量清空类（AI 聊天历史 / 推送历史）不在此列 —— 它们删空也算成功是合理的。

跑法：
  C:\\Users\\X\\.workbuddy\\binaries\\python\\envs\\babycare\\Scripts\\python.exe verify_delete_guard.py
"""

import os
import sys
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

# DATA_DIR / DB_PATH / LOG_DIR 必须在 import server 之前定好
TMP_DIR = tempfile.mkdtemp(prefix="babycare_delguard_verify_")
TMP_DB = os.path.join(TMP_DIR, "babycare.db")
os.environ["BABYCARE_DATA_DIR"] = TMP_DIR
os.environ["BABYCARE_DB_PATH"] = TMP_DB
os.environ["BABYCARE_LOG_DIR"] = os.path.join(TMP_DIR, "logs")

# Windows 没有 fcntl，先打桩再 import server
_fcntl = type(sys)("fcntl")
for _n in ("LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN"):
    setattr(_fcntl, _n, 0)
_fcntl.flock = lambda *a, **k: None
_fcntl.lockf = lambda *a, **k: None
sys.modules["fcntl"] = _fcntl

import server  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f"  -> {extra}" if extra else ""))


print("\n=== 0. 建库 ===")
server.init_db()
try:
    server._run_migrations()
except Exception as e:
    print(f"  [警告] 迁移抛异常（继续）：{e}")
check("临时库建好了", os.path.exists(TMP_DB))

app = server.app
app.config["TESTING"] = True
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True   # 会话登录恒为管理员，与生产同权限

NO = 999999  # 绝不可能存在的 id

# 删除宝宝现在必须先过安全码（防误删）。先设好码，再发 DELETE + body，
# 才能测到「id 不存在 → 404」这一步；否则会停在 403 安全码校验。
r = client.put("/api/settings/safety-code", json={"code": "2580"})
check("设置删除安全码（前置）", r.status_code == 200, r.get_json())

# (方法只会有 DELETE, 路径, 说明)
CASES = [
    # ---- 无 baby 前缀：按 record_id 删 ----
    (f"/api/feeding/{NO}", "喂奶记录"),
    (f"/api/pumping/{NO}", "吸奶记录"),
    (f"/api/sleep/{NO}", "睡眠记录"),
    (f"/api/diaper/{NO}", "尿布记录"),
    (f"/api/growth/{NO}", "成长记录"),
    (f"/api/asq/{NO}", "ASQ 筛查"),
    (f"/api/teething/{NO}", "出牙记录"),
    (f"/api/checkups/{NO}", "体检记录"),
    (f"/api/solid-food/{NO}", "辅食记录"),
    (f"/api/leaps/{NO}", "飞跃期记录"),
    (f"/api/fontanelle/{NO}", "囟门记录"),
    (f"/api/temperatures/{NO}", "体温记录"),
    (f"/api/medications/{NO}", "用药记录"),
    (f"/api/tummy-time/{NO}", "趴卧记录（旧地址）"),
    (f"/api/growth-photos/{NO}", "成长照片"),
    (f"/api/diary/{NO}", "日记"),
    (f"/api/photos/{NO}", "相册照片"),
    (f"/api/knowledge/{NO}", "知识文章"),
    (f"/api/wiki/{NO}", "健康百科"),
    (f"/api/recipes/{NO}", "食谱"),
    (f"/api/activities/log/{NO}", "活动完成记录"),
    (f"/api/vaccines/{NO}", "疫苗记录"),
    (f"/api/health/vaccines/detail/{NO}", "疫苗记录（详情接口）"),
    (f"/api/health/records/detail/{NO}", "健康记录（详情接口）"),
    (f"/api/health/reminders/detail/{NO}", "用药提醒（详情接口）"),
    (f"/api/health/milestones/detail/{NO}", "发育里程碑（详情接口）"),
    (f"/api/health/screenings/detail/{NO}", "发育筛查（详情接口）"),
    (f"/api/health/allergies/detail/{NO}", "过敏记录（详情接口）"),
    (f"/api/health/feeding_summary/detail/{NO}", "喂养小结（详情接口）"),
    (f"/api/product-prices/{NO}", "商品比价"),
    (f"/api/expenses/{NO}", "支出记账"),
    # ---- 带 baby 前缀 ----
    (f"/api/babies/1/milestones/{NO}", "成长成就（里程碑）"),
    (f"/api/babies/1/health-records/{NO}", "健康记录"),
    (f"/api/babies/1/allergy-tests/{NO}", "过敏测试"),
    (f"/api/babies/1/medication-reminders/{NO}", "用药提醒"),
    (f"/api/babies/1/tummytime/{NO}", "趴卧记录"),
    (f"/api/babies/1/tummy-time/{NO}", "趴卧记录（连字符别名）"),
    # ---- 宝宝本体 / 备份文件 ----
    (f"/api/babies/{NO}", "宝宝本体"),
    # 备份文件名有格式白名单（backup_/auto_backup_ 前缀 + .json），
    # 用「格式合法但不存在」的名字，才能测到「文件不存在 → 404」这一步
    ("/api/backup/delete/backup_no_such_file.json", "备份文件"),
]

print("\n=== 1. 删不存在的 id 必须回 404（而不是 200 + success）===")
for url, label in CASES:
    # 宝宝本体：删除带安全码，其余接口不需要
    if url == f"/api/babies/{NO}":
        r = client.delete(url, json={"safety_code": "2580"})
    else:
        r = client.delete(url)
    try:
        d = r.get_json()
    except Exception:
        d = None
    fake_ok = bool(d) and d.get("success") is True
    check(f"{label:<22} DELETE {url}", r.status_code == 404,
          "实际 %d %s" % (r.status_code, (d or {}).get("message")))
    if fake_ok:
        check(f"{label:<22} 不是「假成功」", False,
              "返回了 success=True，库里其实什么都没删")
    else:
        _passed += 1  # 计数用，避免刷屏

print("\n=== 2. 未登录时必须被挡住（不能裸删数据）===")
anon = app.test_client()
for url, label in [(f"/api/temperatures/{NO}", "体温记录"),
                   (f"/api/medications/{NO}", "用药记录"),
                   (f"/api/vaccines/{NO}", "疫苗记录"),
                   (f"/api/babies/1/milestones/{NO}", "成长成就"),
                   (f"/api/photos/{NO}", "相册照片"),
                   (f"/api/babies/{NO}", "宝宝本体")]:
    r = anon.delete(url)
    check(f"{label:<14} 未登录被拒", r.status_code in (401, 403), f"实际 {r.status_code}")

print("\n" + "=" * 62)
print(f"通过 {_passed} 项，失败 {_failed} 项")
print("=" * 62)
sys.exit(1 if _failed else 0)
