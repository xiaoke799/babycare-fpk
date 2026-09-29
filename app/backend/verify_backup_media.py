#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""备份重构冒烟：zip 含相册文件 + 恢复必须安全码。

跑法：$MIMO_PYTHON app/backend/verify_backup_media.py
"""
import os
import sys
import json
import zipfile
import tempfile

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

TMP_DIR = tempfile.mkdtemp(prefix="babycare_backup_media_")
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
    print(f"  [警告] 迁移异常：{e}")

app = server.app
app.config["TESTING"] = True
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True

# 设置安全码
r = client.put("/api/settings/safety-code", json={"code": "2580"})
check("设置安全码", r.status_code == 200, str(r.get_json()))

# 建宝宝 + 假照片文件
r = client.post("/api/babies", json={"name": "备份宝", "birthday": "2024-06-01", "gender": "girl"})
baby_id = r.get_json()["data"]["id"]

photo_dir = os.path.join(TMP_DIR, "photos", str(baby_id))
os.makedirs(photo_dir, exist_ok=True)
rel = f"photos/{baby_id}/test_photo.jpg"
abs_path = os.path.join(TMP_DIR, "photos", str(baby_id), "test_photo.jpg")
with open(abs_path, "wb") as f:
    f.write(b"\xff\xd8\xff\xe0fakejpg")
thumb_rel = f"photos/{baby_id}/thumb_test_photo.jpg"
with open(os.path.join(TMP_DIR, "photos", str(baby_id), "thumb_test_photo.jpg"), "wb") as f:
    f.write(b"\xff\xd8\xff\xe0fakethumb")

import sqlite3
conn = sqlite3.connect(TMP_DB)
conn.execute(
    "INSERT INTO photos (baby_id, photo_date, description, file_path, thumbnail_path) VALUES (?,?,?,?,?)",
    (baby_id, "2024-07-01", "测试照片", rel, thumb_rel),
)
conn.execute(
    "INSERT INTO diary_entries (baby_id, title, content, entry_date) VALUES (?,?,?,?)",
    (baby_id, "第一篇日记", "今天会翻身了", "2024-07-01"),
)
conn.commit()
conn.close()

print("\n=== 1. 创建备份 ===")
r = client.post("/api/backup/create", json={"note": "含相册"})
j = r.get_json() or {}
print("  resp", r.status_code, j.get("filename"), j.get("message"))
check("创建成功", r.status_code == 200 and j.get("success"), str(j))
fname = j.get("filename") or ""
check("备份为 zip", fname.endswith(".zip"), fname)
check("报告媒体数", (j.get("media_count") or 0) >= 2, str(j.get("media_count")))

backup_path = os.path.join(TMP_DIR, "backups", fname)
check("备份文件存在", os.path.isfile(backup_path), backup_path)
with zipfile.ZipFile(backup_path) as zf:
    names = zf.namelist()
    check("包含 data.json", "data.json" in names, str(names))
    check("包含照片文件", any(n.endswith("test_photo.jpg") for n in names), str(names))
    check("包含缩略图", any("thumb_test_photo.jpg" in n for n in names), str(names))
    data = json.loads(zf.read("data.json"))
    check("日记在备份数据中",
          any("diary" in (t or "") for t in (data.get("babies") or [{}])[0].get("records", {}).keys()),
          str(list((data.get("babies") or [{}])[0].get("records", {}).keys())[:20]))

print("\n=== 2. 恢复需安全码 ===")
# 删掉照片文件，恢复后应重新出现
os.remove(abs_path)
os.remove(os.path.join(TMP_DIR, "photos", str(baby_id), "thumb_test_photo.jpg"))

r = client.post("/api/backup/restore", json={"filename": fname})
check("无安全码 403", r.status_code == 403 and (r.get_json() or {}).get("needSafetyCode"),
      f"{r.status_code} {r.get_json()}")

r = client.post("/api/backup/restore", json={"filename": fname, "safety_code": "0000"})
check("错安全码 403", r.status_code == 403, str(r.get_json()))

r = client.post("/api/backup/restore", json={"filename": fname, "safety_code": "2580"})
j = r.get_json() or {}
print("  restore", r.status_code, j.get("message"))
check("正确码恢复成功", r.status_code == 200 and j.get("success"), str(j))
check("相册文件已还原", os.path.isfile(abs_path), abs_path)
check("缩略图已还原", os.path.isfile(os.path.join(TMP_DIR, "photos", str(baby_id), "thumb_test_photo.jpg")))

print("\n=== 3. 旧 JSON 仍可恢复 ===")
legacy = os.path.join(TMP_DIR, "backups", "backup_legacy_test.json")
with open(legacy, "w", encoding="utf-8") as f:
    json.dump({"babies": [], "global": {}, "version": "1.0"}, f)
r = client.post("/api/backup/restore", json={"filename": "backup_legacy_test.json", "safety_code": "2580"})
check("旧 JSON 可恢复", r.status_code == 200, str(r.get_json()))

print(f"\n=== 结果：{_passed} 过 / {_failed} 挂 ===")
sys.exit(1 if _failed else 0)
