#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BabyCare HA 集成的静态自检（不需要装 Home Assistant）。

按 Home Assistant 集成规范检查，不依赖 HA 运行时：
  1. 所有 .py 语法可编译
  2. 所有 .json 可解析，且 manifest 必填字段齐全
  3. strings.json / translations/*.json 的键结构完全一致
     （少一个键 = HA 界面上会出现原始 key 或空白）
  4. config_flow 里用到的 step_id / error / abort 文案键都在 strings.json 里有
  5. 实体类都设了 unique_id（Silver 规则 entity-unique-id）

用法：python verify_ha_integration.py
"""
from __future__ import annotations

import ast
import json
import pathlib
import py_compile
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
COMP = ROOT / "custom_components" / "babycare"

fails: list[str] = []


def chk(ok: bool, label: str, extra: str = "") -> None:
    print(("  [OK]   " if ok else "  [FAIL] ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        fails.append(label)


print("=== 1. Python 语法 ===")
py_files = sorted(COMP.glob("*.py"))
tmpdir = tempfile.mkdtemp(prefix="ha_pycompile_")
for f in py_files:
    try:
        py_compile.compile(str(f), cfile=tmpdir + "/" + f.name + "c", doraise=True)
        chk(True, f.name)
    except py_compile.PyCompileError as e:
        chk(False, f.name, str(e).splitlines()[-1][:90])

print("\n=== 2. JSON 与 manifest ===")
json_files = sorted(COMP.glob("*.json")) + sorted((COMP / "translations").glob("*.json"))
loaded: dict[str, dict] = {}
for f in json_files:
    # ⚠ 必须用 as_posix()：Windows 下 relative_to() 给的是反斜杠，
    #   后面按 "translations/en.json" 取值会取不到（曾导致误报翻译缺失）
    rel = f.relative_to(COMP).as_posix()
    try:
        loaded[rel] = json.loads(f.read_text(encoding="utf-8"))
        chk(True, rel)
    except Exception as e:  # noqa: BLE001
        chk(False, rel, repr(e))

REQUIRED_MANIFEST = ("domain", "name", "codeowners", "config_flow", "documentation",
                     "integration_type", "iot_class", "requirements", "version")
man = loaded.get("manifest.json", {})
missing = [k for k in REQUIRED_MANIFEST if k not in man]
chk(not missing, "manifest 必填字段齐全", f"缺 {missing}" if missing else "")
chk(man.get("domain") == COMP.name, "manifest.domain 与目录名一致",
    f"{man.get('domain')} vs {COMP.name}")
ver = str(man.get("version", ""))
chk(bool(re.match(r"^\d+\.\d+\.\d+$", ver)), "version 是 SemVer", ver)


def keyset(obj, prefix: str = "") -> set[str]:
    """递归收集所有叶子键路径。"""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out |= keyset(v, f"{prefix}.{k}" if prefix else k)
    else:
        out.add(prefix)
    return out


print("\n=== 3. 翻译键结构一致 ===")
src = keyset(loaded.get("strings.json", {}))
for name in ("translations/en.json", "translations/zh-Hans.json"):
    ks = keyset(loaded.get(name, {}))
    if not ks:
        chk(False, f"{name} 存在且非空")
        continue
    chk(ks == src, f"{name} 键与 strings.json 一致",
        f"缺 {sorted(src - ks)} 多 {sorted(ks - src)}" if ks != src else "")

print("\n=== 4. config_flow 引用的文案键都有 ===")
cf_path = COMP / "config_flow.py"
cf_text = cf_path.read_text(encoding="utf-8")
steps = set(re.findall(r'step_id="([a-z_]+)"', cf_text))
# reauth 流程的 step_id 藏在 async_step_xxx 方法名里。
# 但 async_step_reauth 是"流程入口"——它不弹表单、直接转 reauth_confirm，
# 所以 HA 侧不需要为它准备文案，不能把它算进要校验的 step。
methods = set(re.findall(r"async def async_step_(\w+)", cf_text)) - {"reauth"}
steps |= methods
errs = set(re.findall(r'errors\["base"\] = "(\w+)"', cf_text))
aborts = set(re.findall(r'async_abort\(reason="(\w+)"', cf_text))

conf = loaded.get("strings.json", {}).get("config", {})
have_steps = set(conf.get("step", {}))
have_errs = set(conf.get("error", {}))
have_aborts = set(conf.get("abort", {}))

# user 之外的方法名（reauth / reauth_confirm）与 step 名一一对应
for s in sorted(steps - have_steps):
    chk(False, f"step 缺少文案: {s}")
if steps <= have_steps:
    chk(True, f"step 文案齐全（{len(steps)} 个）")
for e in sorted(errs - have_errs):
    chk(False, f"error 缺少文案: {e}")
if errs <= have_errs:
    chk(True, f"error 文案齐全（{sorted(errs)}）")
for a in sorted(aborts - have_aborts):
    chk(False, f"abort 缺少文案: {a}")
if aborts <= have_aborts:
    chk(True, f"abort 文案齐全（{sorted(aborts)}）")

print("\n=== 5. 实体唯一 ID ===")
for name in ("sensor.py", "switch.py"):
    text = (COMP / name).read_text(encoding="utf-8")
    classes = re.findall(r"^class (\w+)\(", text, re.M)
    has_uid = "_attr_unique_id" in text
    chk(has_uid and bool(classes), f"{name} 的实体都设了 unique_id",
        f"{len(classes)} 个实体类")

print("\n=== 6. 设备注册（Gold: devices）===")
has_device = "_attr_device_info" in (COMP / "sensor.py").read_text(encoding="utf-8") \
    and "_attr_device_info" in (COMP / "switch.py").read_text(encoding="utf-8")
chk(has_device, "sensor/switch 都挂了 device_info")
chk((COMP / "entity.py").exists(), "entity.py 提供共享 device_info")

print("\n=== 7. 服务声明与注册一致 ===")
# 不一致的后果：yaml 有 py 没有 → UI 里能选到但调用报"服务不存在"；
# py 有 yaml 没有 → 能调用但 UI 里看不到、也没法配自动化
yaml_text = (COMP / "services.yaml").read_text(encoding="utf-8")
py_text = (COMP / "services.py").read_text(encoding="utf-8")
declared = set(re.findall(r"^([a-z_]+):", yaml_text, re.M))
# 注意写法是 `hass.services.async_register(\n    DOMAIN, "add_feeding", ...`
# —— async_register 后面直接换行，没有 `hass,`，别把正则写成
# `async_register\(\s*hass,\s*DOMAIN`（那样一个都匹配不到，会误报"全部未注册"）
registered = set(re.findall(r"async_register\(\s*DOMAIN\s*,\s*\"([a-z_]+)\"", py_text))
chk(declared == registered, "services.yaml 与 services.py 服务名一致",
    f"只声明未注册 {sorted(declared - registered)}；只注册未声明 {sorted(registered - declared)}"
    if declared != registered else f"{len(declared)} 个服务")

print()
if fails:
    print(f"失败 {len(fails)} 项")
    sys.exit(1)
print("全部通过")
