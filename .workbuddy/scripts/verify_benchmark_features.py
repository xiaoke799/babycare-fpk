# -*- coding: utf-8 -*-
"""新增功能门禁（奶量目标 / 健康随访 / 自定义倒计时）：
迁移 v8 / dashboard.milk_target / 随访 schedule+plan / countdowns CRUD / babies PUT is_premature
铁律：BABYCARE_DB_PATH 必须在 import server 之前设，否则静默打生产库。
运行：C:/Users/X/.workbuddy/binaries/python/envs/babycare/Scripts/python.exe .workbuddy/scripts/verify_benchmark_features.py
"""
import os
import sys
import tempfile
import json
import datetime

# ==================== 日期锚点（2026-10-17 修）====================
# 本门禁曾把「今天」写死成常量（生日 2026-04-16 / 记录日期 2026-10-16 /
# planned_date 2026-10-20 + days_left==4 / 6 月龄档 days_left==0）。午夜一过全部
# 漂移：10-17 实测 4 项集体变红（今日实际归零、剩余天数差 1 天、6 月龄档滑成
# 8 月龄）。产品逻辑是对的，错的是断言（同 verify_periodic_report 月报病的复发）。
# 现在所有日期从运行时的「今天」推导，与跑门禁的真实日期无关：
#   _BIRTHDAY = 今天-183 天 ⇒ 日龄恒 183（>180 且 int(round(183/30.44,1)-6)==0
#     ⇒ 奶量档恒 800），6 月龄建议日 = 出生+round(6×30.44)=183 天 = 今天
#     ⇒ 6 月龄档恒「今天到档」（days_left==0，挑选条件 days_left>=0 成立）。
# BENCH_PROBE_BIRTHDAY_SHIFT=N：负向对照探针，把生日锚点平移 N 天，
#   期望「dashboard 带下次随访(6月龄)」转红（6 月龄档移出今天 ⇒ 下次变 8 月龄，
#   或 days_left 变 1）——证明该断言不是恒真。用完务必置回 0。
# ⚠ 不提供 BENCH_FAKE_TODAY：后端 datetime.date.today() 读真实时钟，
#   只改门禁侧的「今天」会造出一个两边不对账的假测试。
_TODAY = datetime.date.today()


def _d(delta):
    """相对锚定「今天」的日期 ISO 串（delta 单位：天）。"""
    return (_TODAY + datetime.timedelta(days=delta)).isoformat()


_BIRTHDAY = _d(-183 + int(os.environ.get("BENCH_PROBE_BIRTHDAY_SHIFT", "0")))
_BIRTH = datetime.date.fromisoformat(_BIRTHDAY)
_GROWTH_DATE = _d(0)               # 成长记录/喂养记录都记「今天」
_FEED_START = _d(0) + " 08:00:00"
_S1 = (_BIRTH + datetime.timedelta(days=30)).isoformat()   # 1 月龄建议日 = 出生 + round(1×30.44)=30 天
_CHECKUP_DATE = (_BIRTH + datetime.timedelta(days=34)).isoformat()  # 落 1 月龄对账窗口 [建议-15, 建议+60] 内
_PLAN_DATE = _d(4)                 # 预约日 = 今天+4 ⇒ days_left 恒 4
_FIRST_BIRTHDAY = (_BIRTH + datetime.timedelta(days=365)).isoformat()  # 周岁（未来倒计时用）
_SOON_DATE = _d(9)                 # 近期倒计时（原写死 2026-10-25 = 当天+9）

_TMP = tempfile.mkdtemp(prefix="babycare_bench_")
os.environ["BABYCARE_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ.setdefault("BABYCARE_DATA_DIR", _TMP)
os.environ.setdefault("BABYCARE_DEV_AUTH", "1")

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO, "app", "backend"))

import server  # noqa: E402  （必须在环境变量之后）

app = server.app
app.config["TESTING"] = True

FAIL = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAIL.append(name)


with app.test_client() as c:
    # 开发登录：直接种 session（同 verify_baby_manage.py 的做法）
    with c.session_transaction() as sess:
        sess["authenticated"] = True

    # 建宝宝
    r = c.post("/api/babies", json={"name": "测试宝宝A", "birthday": _BIRTHDAY, "gender": "girl"})
    check("新建宝宝", r.status_code == 200 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:200]}")
    bid = r.get_json()["data"]["id"]

    # 成长记录（6.5kg）→ 奶量按体重估算（记今天，保证「今日实际」能统计到）
    r = c.post(f"/api/babies/{bid}/growth", json={"record_date": _GROWTH_DATE, "weight": 6.5, "height": 62})
    check("新建成长记录(6.5kg)", r.status_code == 200, f"{r.status_code} {r.get_data(as_text=True)[:200]}")

    # 喂养记录（今日 300ml）
    r = c.post(f"/api/babies/{bid}/feeding", json={"start_time": _FEED_START, "feeding_type": "bottle", "amount": 300})
    check("新建喂养记录(300ml)", r.status_code == 200, f"{r.status_code} {r.get_data(as_text=True)[:200]}")

    # dashboard.milk_target
    r = c.get(f"/api/babies/{bid}/dashboard")
    data = r.get_json().get("data") or {}
    mt = data.get("milk_target")
    check("dashboard 含 milk_target", mt is not None, json.dumps(data.get("milk_target"), ensure_ascii=False)[:200])
    if mt:
        # 日龄恒 183 天 = 6.0 月龄刚过，落 6-12 月档首档 800ml（辅食渐入）
        check("6月龄档=800(辅食渐入)", mt["target_ml"] == 800 and mt["method"] == "auto", json.dumps(mt, ensure_ascii=False))
        check("今日实际=300", mt["today_amount"] == 300, str(mt["today_amount"]))
        check("达成率=38", mt["percent"] == 38, str(mt["percent"]))
    check("dashboard 含 upcoming_countdowns", isinstance(data.get("upcoming_countdowns"), list))

    # 无体重宝宝 → no-weight 档
    r = c.post("/api/babies", json={"name": "无体重宝", "birthday": "2026-09-16", "gender": "boy"})
    bid2 = r.get_json()["data"]["id"]
    r = c.get(f"/api/babies/{bid2}/dashboard")
    mt2 = (r.get_json().get("data") or {}).get("milk_target")
    check("无体重走 no-weight 档", mt2 and mt2["method"] == "no-weight", json.dumps(mt2, ensure_ascii=False)[:200])

    # 随访：足月宝不含早产档，早产宝含
    r = c.get(f"/api/health/followup/schedule/{bid}")
    d = r.get_json().get("data") or {}
    check("随访清单 12 档(足月)", len(d.get("items", [])) == 12 and d.get("is_premature") is False, f"items={len(d.get('items', []))}")
    r = c.put(f"/api/babies/{bid}", json={"is_premature": True})
    check("宝宝标记早产", r.status_code == 200 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:200]}")
    r = c.get(f"/api/health/followup/schedule/{bid}")
    d = r.get_json().get("data") or {}
    check("早产宝 18 档", len(d.get("items", [])) == 18, f"items={len(d.get('items', []))}")

    # 建议日期正确性：1月龄 = 出生 + round(30.44)=30 天（锚点推导，不写死）
    item1 = next((i for i in d["items"] if i["age_months"] == 1), None)
    check("1月龄建议日期=出生+30天", item1 and item1["suggested_date"] == _S1, str(item1 and item1["suggested_date"]))

    # 预约 upsert + 取消
    r = c.put(f"/api/health/followup/plan/{bid}", json={"age_months": 6, "planned_date": _PLAN_DATE, "note": "区妇幼"})
    check("设置预约", r.status_code == 200 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:200]}")
    r = c.get(f"/api/health/followup/schedule/{bid}")
    item6 = next((i for i in (r.get_json().get("data") or {}).get("items", []) if i["age_months"] == 6), None)
    check("预约日期落库+剩余天数", item6 and item6["planned_date"] == _PLAN_DATE and item6["days_left"] == 4, json.dumps(item6, ensure_ascii=False)[:200])
    r = c.put(f"/api/health/followup/plan/{bid}", json={"age_months": 999, "planned_date": _PLAN_DATE})
    check("非法月龄档被拒", r.status_code == 400, str(r.status_code))
    r = c.put(f"/api/health/followup/plan/{bid}", json={"age_months": 6, "planned_date": "10月20号"})
    check("脏日期被拒", r.status_code == 400, str(r.status_code))
    r = c.delete(f"/api/health/followup/plan/{bid}?age_months=6")
    check("取消预约", r.status_code == 200, f"{r.status_code} {r.get_data(as_text=True)[:120]}")
    r = c.get(f"/api/health/followup/schedule/{bid}")
    item6 = next((i for i in (r.get_json().get("data") or {}).get("items", []) if i["age_months"] == 6), None)
    check("取消后 planned 为空", item6 and item6["planned_date"] is None, json.dumps(item6, ensure_ascii=False)[:160])

    # 对账：1 月龄档建议 = 出生+30 天，插入窗口 [建议-15, 建议+60] 内的体检记录 → 该档标记 done
    r = c.post(f"/api/health/records/{bid}", json={"record_date": _CHECKUP_DATE, "title": "满月体检", "record_type": "routine"})
    check("插入门窗内体检记录", r.status_code == 200 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:160]}")
    r = c.get(f"/api/health/followup/schedule/{bid}")
    d = r.get_json().get("data") or {}
    item1 = next((i for i in d.get("items", []) if i["age_months"] == 1), None)
    item3 = next((i for i in d.get("items", []) if i["age_months"] == 3), None)
    check("1月龄档对账为已完成", item1 and item1["status"] == "done", str(item1 and item1["status"]))
    check("3月龄档不受牵连", item3 and item3["status"] == "planned", str(item3 and item3["status"]))

    # 自定义奶量目标：设置 → 优先于自动估算；越界拒绝；清除回落
    r = c.put(f"/api/babies/{bid}", json={"custom_milk_target": 750})
    check("设置自定义奶量目标", r.status_code == 200 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:160]}")
    r = c.get(f"/api/babies/{bid}/dashboard")
    mt = (r.get_json().get("data") or {}).get("milk_target") or {}
    check("自定义目标优先(750)", mt.get("method") == "custom" and mt.get("target_ml") == 750, json.dumps(mt, ensure_ascii=False)[:160])
    r = c.put(f"/api/babies/{bid}", json={"custom_milk_target": 9999})
    check("越界目标被拒", r.status_code == 400, str(r.status_code))
    r = c.put(f"/api/babies/{bid}", json={"custom_milk_target": None})
    check("清除目标", r.status_code == 200, str(r.status_code))
    r = c.get(f"/api/babies/{bid}/dashboard")
    mt = (r.get_json().get("data") or {}).get("milk_target") or {}
    check("清除后回落自动估算", mt.get("method") == "auto", json.dumps(mt, ensure_ascii=False)[:160])

    # dashboard.upcoming_followup：早产宝的 42天/4/5 月龄档已过，最早未完成档是 6 月龄（今天到档）
    r = c.get(f"/api/babies/{bid}/dashboard")
    fu = (r.get_json().get("data") or {}).get("upcoming_followup")
    check("dashboard 带下次随访(6月龄)", fu and fu["label"] == "6月龄" and fu["days_left"] == 0, json.dumps(fu, ensure_ascii=False)[:160])

    # 倒计时 CRUD
    r = c.post(f"/api/babies/{bid}/countdowns", json={"title": "周岁生日", "target_date": _FIRST_BIRTHDAY, "note": "准备蛋糕"})
    check("建倒计时", r.status_code == 201 and r.get_json().get("success"), f"{r.status_code} {r.get_data(as_text=True)[:200]}")
    cid = r.get_json()["data"]["id"]
    r = c.post(f"/api/babies/{bid}/countdowns", json={"title": "", "target_date": "2027-04-16"})
    check("空标题被拒", r.status_code == 400, str(r.status_code))
    r = c.post(f"/api/babies/{bid}/countdowns", json={"title": "脏日期", "target_date": "下周三"})
    check("脏目标日期被拒", r.status_code == 400, str(r.status_code))
    r = c.put(f"/api/babies/{bid}/countdowns/{cid}", json={"note": "买礼物"})
    check("改倒计时", r.status_code == 200 and r.get_json()["data"]["note"] == "买礼物", f"{r.status_code}")
    r = c.get(f"/api/babies/{bid}/countdowns")
    check("清单回读", len(r.get_json().get("data", [])) == 1, str(len(r.get_json().get("data", []))))
    r = c.delete(f"/api/babies/{bid}/countdowns/{cid}")
    check("删倒计时", r.status_code == 200 and r.get_json().get("deleted") == 1, f"{r.status_code}")
    r = c.delete(f"/api/babies/{bid}/countdowns/{cid}")
    check("重复删报404(假成功守卫)", r.status_code == 404, str(r.status_code))

    # dashboard.upcoming_countdowns 出现未来事件
    c.post(f"/api/babies/{bid}/countdowns", json={"title": "体检", "target_date": _SOON_DATE})
    r = c.get(f"/api/babies/{bid}/dashboard")
    up = (r.get_json().get("data") or {}).get("upcoming_countdowns")
    check("dashboard 带未来倒计时", isinstance(up, list) and len(up) == 1 and up[0]["title"] == "体检", json.dumps(up, ensure_ascii=False)[:200])

print()
if FAIL:
    print(f"共 {len(FAIL)} 项失败：{FAIL}")
    sys.exit(1)
print("全部通过")
