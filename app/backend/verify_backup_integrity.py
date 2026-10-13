#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「备份可恢复性校验」真的有用。

背景：备份文件生成后就躺在那儿，从来没人能回答「它还能不能用」。
真到要恢复那天才发现坏了就晚了。本脚本正向 + 负向各验一遍：
  正向 —— 刚生成的备份校验通过
  负向 —— 真的动手弄坏它（删行 / 截断 / 伪造相册数），确认校验能抓到
外加一次**真恢复演练**：把备份恢复进一个空库，比对行数是否一致。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if "fcntl" not in sys.modules:
    m = types.ModuleType("fcntl")
    m.LOCK_EX, m.LOCK_NB, m.LOCK_UN = 2, 4, 8
    m.flock = lambda *a, **k: None
    sys.modules["fcntl"] = m

tmpdir = tempfile.mkdtemp(prefix="babycare_backup_")
os.environ["BABYCARE_DATA_DIR"] = tmpdir
os.environ["BABYCARE_DB_PATH"] = os.path.join(tmpdir, "babycare.db")
os.environ["BABYCARE_CONFIG_DIR"] = tmpdir

import server  # noqa: E402

app = server.app
app.config["TESTING"] = True
server.init_db()

# 第 4 段要真打恢复接口，需要登录态 + 安全码
client = app.test_client()
with client.session_transaction() as sess:
    sess["authenticated"] = True
    sess["is_admin"] = True
    sess["user_id"] = 1
client.put("/api/settings/safety-code", json={"code": "2580"})

from blueprints.backup import (  # noqa: E402
    _RESTORE_ALLOWED_TABLES, _restore_from_data, inspect_backup_archive,
    write_backup_archive)
from utils import get_db  # noqa: E402

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


work = tempfile.mkdtemp(prefix="babycare_bp_files_")


def make_backup(name, mutate=None):
    """造一份备份；mutate 用来故意弄坏它。

    ⚠ 结构必须照真实的备份格式来：**按宝宝分组**
        {"babies": [{"baby": {...}, "records": {表名: [行]}}]}
    最初这里写成了顶层平铺 {表名: [行]}，结果「恢复演练」里喂养/尿布一条都没恢复出来，
    才发现统计口径全错。备份格式不是自己定的，是 _restore_from_data 决定的。
    """
    all_data = {
        'babies': [{
            'baby': {'name': '小甲', 'birthday': '2026-01-01', 'gender': 'boy',
                     'due_date': None, 'avatar': ''},
            'records': {
                'feeding_records': [
                    {'baby_id': 1, 'start_time': '2026-10-01 08:00:00',
                     'feeding_type': 'bottle', 'amount': 120},
                    {'baby_id': 1, 'start_time': '2026-10-01 12:00:00',
                     'feeding_type': 'bottle', 'amount': 150},
                ],
                'diaper_records': [
                    {'baby_id': 1, 'change_time': '2026-10-01 09:00:00',
                     'diaper_type': 'wet'},
                ],
            },
        }],
    }
    path = os.path.join(work, name)
    # 写备份要读库（相册扫描 + 源库完整性检查），必须在应用上下文里
    with app.app_context():
        write_backup_archive(path, all_data)
    if mutate:
        mutate(path)
    return path


print("=== 1. 正向：刚生成的备份 ===")
good = make_backup("backup_good.zip")
rep = inspect_backup_archive(good)
chk(rep['status'] == 'ok', "校验通过", f"实际 {rep['status']}：{rep['message']}")
chk(rep['ok'] is True, "ok 标志为真")
chk(rep['tables_matched'] == rep['tables_checked'] > 0,
    f"逐表比对 {rep['tables_checked']} 张表全一致",
    f"匹配 {rep['tables_matched']}")
chk(rep['mismatches'] == [], "没有不一致项")
with zipfile.ZipFile(good) as zf:
    chk('manifest.json' in zf.namelist(), "备份包里有 manifest.json")
    mf = json.loads(zf.read('manifest.json').decode('utf-8'))
chk(mf['tables'].get('feeding_records') == 2, "清单记下了喂奶 2 条",
    f"实际 {mf['tables'].get('feeding_records')}")
chk(mf['baby_count'] == 1, "清单记下了 1 个宝宝")
chk(mf.get('source_integrity') == 'ok', "源库完整性检查结果是 ok",
    f"实际 {mf.get('source_integrity')}")

print("\n=== 2. 负向：动手弄坏它，看校验抓不抓得住 ===")


def drop_rows(path):
    """把某张表少一条（模拟拷贝不全/被截断）"""
    with zipfile.ZipFile(path) as zf:
        data = json.loads(zf.read('data.json').decode('utf-8'))
        manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
    data['babies'][0]['records']['feeding_records'] = \
        data['babies'][0]['records']['feeding_records'][:1]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', json.dumps(data, ensure_ascii=False, indent=2))
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))


def tamper_value(path):
    """行数不变、只改一个数值（奶量 120 → 999）。

    这是更隐蔽的一类损坏：光比行数**发现不了**，必须靠 data.json 的内容哈希。
    """
    with zipfile.ZipFile(path) as zf:
        data = json.loads(zf.read('data.json').decode('utf-8'))
        manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
    data['babies'][0]['records']['feeding_records'][0]['amount'] = 999
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', json.dumps(data, ensure_ascii=False, indent=2))
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))


bad1 = make_backup("backup_dropped.zip", drop_rows)
rep = inspect_backup_archive(bad1)
chk(rep['status'] == 'broken', "少了一条记录 → 判为 broken", f"实际 {rep['status']}")
chk(rep['ok'] is False, "ok 标志为假")
names = [m['table'] for m in rep['mismatches']]
chk('feeding_records' in names, "明确指出是哪张表对不上", f"实际 {names}")
mism = next(m for m in rep['mismatches'] if m['table'] == 'feeding_records')
chk(mism['expected'] == 2 and mism['actual'] == 1,
    "报出期望值与实际值", f"期望 {mism['expected']} 实际 {mism['actual']}")


def break_crc(path):
    """模拟拷贝/传输损坏：翻转文件中段的字节，zip 的 CRC 校验应当报错。

    ⚠ 曾经写成「找到 b'feeding_records' 再改」——data.json 是 DEFLATE 压缩的，
    压缩流里根本找不到这段明文，find 返回 -1，于是**一个字节都没改**，
    备份完好无损，测试却以为「改坏了」——负向对照必须真的破坏成功，
    否则验证的是「没破坏也能通过」，等于自己骗自己。
    """
    with open(path, 'rb') as f:
        raw = bytearray(f.read())
    mid = len(raw) // 2
    for i in range(mid, mid + 6):
        raw[i] = (raw[i] + 1) % 256
    with open(path, 'wb') as f:
        f.write(raw)


bad2 = make_backup("backup_tampered.zip", tamper_value)
rep = inspect_backup_archive(bad2)
chk(rep['status'] == 'broken',
    "值被改但行数没变 → 靠内容哈希抓出来", f"实际 {rep['status']}")
chk(rep['tables_matched'] == rep['tables_checked'],
    "这类损坏行数是一致的（所以光比行数抓不到）")
hash_rows = [m['table'] for m in rep['mismatches'] if '哈希' in m['table']]
chk(hash_rows, "明确指出内容哈希不一致", f"实际不一致项 {[m['table'] for m in rep['mismatches']]}")


def drop_manifest(path):
    """模拟旧版本备份：只有 data.json，没有清单"""
    with zipfile.ZipFile(path) as zf:
        data = zf.read('data.json')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('data.json', data)


old = make_backup("backup_old.zip", drop_manifest)
rep = inspect_backup_archive(old)
chk(rep['status'] == 'no_manifest',
    "旧版备份（无清单）走兼容路径而不是报错", f"实际 {rep['status']}")
chk(rep['ok'] is True, "旧版备份仍判为可用")

notzip = os.path.join(work, "backup_broken.zip")
with open(notzip, 'w') as f:
    f.write("这不是 zip")
rep = inspect_backup_archive(notzip)
chk(rep['status'] == 'broken' and not rep['ok'],
    "根本不是 zip 的文件 → broken", f"实际 {rep['status']}")

print("\n=== 3. 真恢复演练：备份恢复进空库，行数要能对上 ===")
with zipfile.ZipFile(good) as zf:
    data = json.loads(zf.read('data.json').decode('utf-8'))
con = sqlite3.connect(":memory:")
con.row_factory = sqlite3.Row
con.execute("PRAGMA foreign_keys=OFF")
# 用**真实表结构**建表，而不是拍脑袋 CREATE TABLE (... baby_id)：
# 恢复逻辑依赖列名（baby.name / baby.birthday），结构不对就不是一次真实演练了
with app.app_context():
    ddl_rows = get_db().execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL").fetchall()
created = 0
for r in ddl_rows:
    try:
        con.execute(r['sql'])
        created += 1
    except sqlite3.OperationalError:
        pass
con.commit()
chk(created > 10, f"按真实结构建了 {created} 张表")
restored, failed = _restore_from_data(con, data)
con.commit()
got_feed = con.execute("SELECT COUNT(*) FROM feeding_records").fetchone()[0]
got_baby = con.execute("SELECT COUNT(*) FROM babies").fetchone()[0]
got_diaper = con.execute("SELECT COUNT(*) FROM diaper_records").fetchone()[0]
chk(got_baby == 1, "恢复到空库后宝宝 1 个", f"实际 {got_baby}")
chk(got_feed == 2, "恢复到空库后喂奶 2 条", f"实际 {got_feed}")
chk(got_diaper == 1, "恢复到空库后尿布 1 条", f"实际 {got_diaper}")
chk(not failed, "恢复过程没有失败项", f"失败 {failed}")

# 演练的真正意义：清单说的行数 == 恢复出来的行数
chk(mf['tables'].get('feeding_records') == got_feed,
    "清单声明的行数与实际恢复出的行数一致（清单可信）")
con.close()

print("\n=== 4. 恢复入口的 pre-flight：损坏备份必须被拒，且不许动现有数据 ===")
# inspect_backup_archive 早就写好了，但 2026-10-12 审计发现三个恢复入口
# （restore / upload-restore / restore-from-share）都没调用它 —— 而
# _restore_from_data 对同名宝宝是「先删后插」：损坏备份只要 JSON 能解析
# 就会通过，结果是删掉现有记录 + 写入残本后 commit，静默数据丢失。
# 机制在、链路没接上 = 没有。这里把链路钉住。

# 4a. 库里先放点真数据（恢复被拒时它们必须原样活着）
client.post("/api/babies", json={"name": "存量宝宝", "birthday": "2025-03-01"})
for _amt in (120, 150):        # 两条，drop_rows 删掉一条才会真的变坏
    r_feed = client.post("/api/babies/1/feeding", json={
        "feeding_type": "bottle", "start_time": "2026-10-01 08:00:00", "amount": _amt})
chk(r_feed.status_code == 200, "先造两条存量喂奶记录", str(r_feed.get_json()))
with app.app_context():
    n_before = get_db().execute("SELECT COUNT(*) FROM feeding_records").fetchone()[0]
chk(n_before == 2, "存量喂奶 2 条", f"实际 {n_before}")

# 4b. 造一份真备份（走接口，落在真实备份目录），然后弄坏它
r = client.post("/api/backup/create", json={"note": "pre-flight 测试"})
j_create = r.get_json() or {}
chk(r.status_code == 200 and j_create.get("success"), "通过接口创建备份", str(j_create))
good_name = j_create.get("filename") or ""
corrupt_name = good_name.replace(".zip", "") + "_corrupt.zip"
corrupt_path = os.path.join(tmpdir, "backups", corrupt_name)
shutil.copyfile(os.path.join(tmpdir, "backups", good_name), corrupt_path)
drop_rows(corrupt_path)          # 复用第 2 段的破坏函数：删一行喂奶记录
rep = inspect_backup_archive(corrupt_path)
chk(rep['status'] == 'broken', "确认这份备份已被弄坏", f"实际 {rep['status']}")

# 4c. 恢复它：必须 400 拒绝，且存量数据一条不许少
r = client.post("/api/backup/restore",
                json={"filename": corrupt_name, "safety_code": "2580"})
j = r.get_json() or {}
chk(r.status_code == 400, "损坏备份被恢复接口拒绝（400）", f"{r.status_code} {j}")
chk("校验" in str(j.get("message") or ""), "拒绝原因说清楚是校验未通过", str(j.get("message")))
with app.app_context():
    n_after = get_db().execute("SELECT COUNT(*) FROM feeding_records").fetchone()[0]
chk(n_after == n_before and n_before >= 1,
    "被拒后存量数据原样保留（先删后插没有发生）", f"前 {n_before} 后 {n_after}")

# 4d. 共享目录恢复入口同样要挡（用户手工拷进去的备份损坏概率更高）
r = client.post("/api/backup/restore-from-share",
                json={"filename": corrupt_name, "safety_code": "2580"})
chk(r.status_code == 400, "restore-from-share 同样拒绝损坏备份",
    f"{r.status_code} {r.get_json()}")

# 4e. 没弄坏的同一份备份必须能正常恢复（证明不是「一律拒绝」）
r = client.post("/api/backup/restore",
                json={"filename": good_name, "safety_code": "2580"})
chk(r.status_code == 200 and (r.get_json() or {}).get("success"),
    "完好备份仍能正常恢复（pre-flight 不误伤）", str(r.get_json()))

print()
if fails:
    print("失败 %d 项" % len(fails))
    sys.exit(1)
print("通过")
