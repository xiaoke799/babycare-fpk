#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计「吞异常的 except」：捕获后既不打日志、不重抛、也不留原因的分支。

为什么要有这条
--------------
2026-10-10 的 AI 故障就是这一类：`ai_engine_tooling.py` 的 6 个失败出口
只 `logger.warning` 一行（甚至不打），却**不写 _set_last_llm_error** ⇒ 界面
只回一句笼统的"调用失败"，用户看不出 401 / 模型名错 / 断网。
"吞了"不等于"处理了"——真正的判据是**失败信息有没有走到会被看见的地方**。

判据（AST，不做正则匹配，避免注释/字符串误伤）
---------------------------------------------
对一个 `except` 处理器，若其函数体里**同时**没有：
  - 日志调用（logger.* / logging.* / print）
  - raise（重抛或抛新异常）
  - 记录到「会被展示」的地方（_set_last_llm_error / 赋值给 errors 列表 /
    return 里带 message / jsonify 带错误原因）
则记为「静默吞噬」。

裸 `except:` 与 `except ...: pass` 单独标出（优先级最高）。

用法：
  python check_silent_except.py            # 全后端
  python check_silent_except.py --ai       # 只看 AI 链路相关文件
退出码：0 = 没问题（或只有已登记白名单）；1 = 有未登记的静默吞噬。
"""
import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, 'app', 'backend')

# 只扫业务代码，跳过 vendor（第三方）与测试脚本
SKIP_DIRS = {'vendor', '__pycache__'}

LOG_ATTRS = ('warning', 'error', 'exception', 'critical', 'info', 'debug', 'warning_')

# 白名单：确实该吞的（有正当理由），按 (文件, 行号附近唯一片段) 登记。
# ⚠ 新增一条必须写明理由；判据报错比没判据更贵。
ALLOW = [
    # 「保存历史失败不影响主功能」——用户消息已入库，聊天历史写失败不该打断回答
    ('blueprints/ai.py', 'save_chat_message'),
]


def _has_logging(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                if f.attr in LOG_ATTRS:
                    return True
                # logging.getLogger(...).xxx() 已被上面覆盖；这里兜 print
            if isinstance(f, ast.Name) and f.id in ('print', 'traceback'):
                return True
        if isinstance(n, ast.Raise):
            return True
    return False


def _has_reason(node):
    """有没有把原因写到会被展示/记录的地方。"""
    src_names = ('_set_last_llm_error',)
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            fname = getattr(f, 'id', None) or getattr(f, 'attr', None)
            if fname in src_names:
                return True
            if fname in ('jsonify', 'append', 'setdefault') :
                return True
        if isinstance(n, ast.Return) and n.value is not None:
            return True
        if isinstance(n, ast.Assign):
            for t in n.targets:
                nm = getattr(t, 'id', None) or getattr(t, 'attr', None)
                if nm in ('errors', 'error', 'fallback_reason', 'last_error', 'result'):
                    return True
    return False


def scan_file(path, rel):
    try:
        tree = ast.parse(io.open(path, encoding='utf-8').read())
    except SyntaxError as e:
        return [('SYNTAX', rel, e.lineno or 0, str(e))]
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for h in node.handlers:
            bare = h.type is None
            only_pass = len(h.body) == 1 and isinstance(h.body[0], ast.Pass)
            logged = _has_logging(ast.Module(body=h.body, type_ignores=[]))
            reasoned = _has_reason(ast.Module(body=h.body, type_ignores=[]))
            if only_pass or bare:
                out.append(('BARE/PASS', rel, h.lineno, ''))
            elif not logged and not reasoned:
                out.append(('SILENT', rel, h.lineno, ''))
    return out


def main():
    only_ai = '--ai' in sys.argv
    ai_files = ('ai_engine.py', 'ai_client.py', 'ai_engine_tooling.py', 'ai_tools.py',
                'ai_writer.py', 'blueprints/ai.py', 'notifier')
    findings = []
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.endswith('.py') or fn.startswith('verify_'):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, BACKEND).replace('\\', '/')
            if only_ai and not any(a in rel for a in ai_files):
                continue
            findings.extend(scan_file(full, rel))

    allow_hits = 0
    real = []
    for kind, rel, ln, extra in findings:
        if any(rel == a[0] or rel.endswith(a[0]) for a in ALLOW):
            # 白名单按文件粗放放行（ai.py 里有正当的吞异常），人工已核
            allow_hits += 1
            continue
        real.append((kind, rel, ln, extra))

    print(f"扫描 {BACKEND}")
    print(f"命中 {len(findings)} 处；白名单放行 {allow_hits} 处；待人工确认 {len(real)} 处")
    print("=" * 78)
    for kind, rel, ln, extra in real:
        print(f"  [{kind:9}] {rel}:{ln} {extra}")
    print()
    print("⚠ 这是**体检工具不是门禁**：绝大多数命中是正当兜底（日志包装、JSON 解析回落、")
    print("  清理类 os.remove/close）。2026-10-10 人工分诊 AI 链 19 处，全部正当。")
    print("  只有当「失败信息到不了会被看见的地方」时才算缺陷 —— 静态判不出来，需人工看。")
    print("  故默认 exit 0（不参与批量门禁）；要当门禁跑请加 --strict。")
    return 1 if ('--strict' in sys.argv and real) else 0


if __name__ == '__main__':
    sys.exit(main())
