#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地「假厂商」——OpenAI 兼容的最小 chat/completions 服务，供离线端到端验证 AI 链路。

为什么需要它
------------
AI 链路的缺陷（错误分类、超时、空响应、工具循环、max_tokens 是否生效）都发生在
「真的发出去一个 HTTP 请求」之后。不外网、不要真 Key 就没法验证，于是只能靠读代码 ——
而 2026-10-10 的故障（工具分支漏记失败原因）恰恰是读代码看不出来的那类。

用法（在门禁里起线程）：
    srv, port = start_mock()
    LLM['api_url'] = f'http://127.0.0.1:{port}/v1/chat/completions'
    ... 跑断言 ...
    srv.shutdown()

行为由请求体里的 `model` 决定（这样不改 URL、不改代码就能切场景）：
    ok-*        正常返回一段文本
    tool-*      第 1 轮返回 tool_calls，之后返回文本（模拟工具调用闭环）
    err401/404/429/500   返回对应状态码 + 类厂商的 JSON 错误体
    timeout     挂起不返回（验证超时/连接异常分支）
    empty       200 但 choices 为空
    nomsg       200 但结构里没有 content
    huge        max_tokens 很大时返回固定长文本（验证截断/长度）
另外提供控制口：
    GET  /__seen         返回历次请求的摘要（含 Authorization / max_tokens / model 是否收到）
    POST /__reset        清空记录
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SEEN = []
_MODE = {'force': None}     # 全局强制模式：绕开「模型名」这条通道


def set_mode(mode):
    """强制所有请求都按 mode 应答（None = 恢复按 model 名分派）。

    为什么需要：应用每个请求都会 _ensure_llm_applied() 从磁盘重新同步 LLM 配置，
    在进程内直接改 LLM['model'] 会被它覆盖掉。用全局模式开关，
    验证就与「模型名恰好等于某个字符串」解耦了。
    """
    _MODE['force'] = mode


def _reply_for(model: str, payload: dict):
    """按 model 名给出 (status, body_dict)。返回 None 表示要挂起。"""
    m = _MODE['force'] or (model or '').strip()
    if m == 'timeout':
        return 'HANG'
    if m == 'err401':
        return 401, {'error': {'message': 'Authentication Fails, Your api key is invalid',
                               'type': 'authentication_error'}}
    if m == 'err404':
        return 404, {'error': {'message': 'Model Not Exist', 'type': 'invalid_request_error'}}
    if m == 'err429':
        return 429, {'error': {'message': 'Rate limit reached', 'type': 'rate_limit_error'}}
    if m == 'err500':
        return 500, {'error': {'message': 'internal server error', 'type': 'server_error'}}
    if m == 'empty':
        return 200, {'id': 'x', 'choices': []}
    if m == 'nomsg':
        return 200, {'id': 'x', 'choices': [{'index': 0}]}
    if m.startswith('tool'):
        # 同一轮里如果已经出现过 tool 结果，就直接给最终文本，形成闭环
        msgs = payload.get('messages') or []
        if any(x.get('role') == 'tool' for x in msgs):
            return 200, {'id': 'x', 'choices': [{'index': 0, 'message':
                          {'role': 'assistant', 'content': '工具已执行，这是最终回答。'}}]}
        return 200, {'id': 'x', 'choices': [{'index': 0, 'message': {
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call_1', 'type': 'function',
                            'function': {'name': 'query_baby_info', 'arguments': '{}'}}]}}]}
    if m == 'long':
        return 200, {'id': 'x', 'choices': [{'index': 0, 'message': {
            'role': 'assistant', 'content': '长文本' * 400}}]}
    if m == 'json-bad':
        # 调用成功、但返回的不是可解析的 JSON —— 用来验证
        # 「无法识别」与「调用失败」两种文案不会串味
        return 200, {'id': 'x', 'choices': [{'index': 0, 'message': {
            'role': 'assistant', 'content': '抱歉，我不太明白您的意思。'}}]}
    if m == 'json-ok':
        # 合法的喂养记录 JSON（含 ```json 围栏，走真实的解析分支）
        body = ('```json\n'
                '{"feeding_type": "bottle", "amount": 180, '
                '"duration_minutes": null, "note": "假厂商返回"}\n```')
        return 200, {'id': 'x', 'choices': [{'index': 0, 'message': {
            'role': 'assistant', 'content': body}}]}
    # 默认 / ok-*
    return 200, {'id': 'x', 'choices': [{'index': 0, 'message': {
        'role': 'assistant', 'content': '这是来自假厂商的正常回答。'}}]}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):      # 静音
        pass

    def _send(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/__seen'):
            self._send(200, {'count': len(_SEEN), 'seen': _SEEN})
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        if self.path.startswith('/__reset'):
            _SEEN.clear()
            self._send(200, {'ok': True})
            return
        if self.path.startswith('/__mode'):
            try:
                set_mode((json.loads(raw.decode('utf-8')) or {}).get('mode'))
            except Exception:
                set_mode(None)
            self._send(200, {'ok': True, 'mode': _MODE['force']})
            return
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            self._send(400, {'error': 'bad json'})
            return

        _SEEN.append({
            'path': self.path,
            'model': payload.get('model'),
            'max_tokens': payload.get('max_tokens'),
            # ollama 把回复上限放在 options.num_predict（2026-10-12：
            # _call_ollama 曾收了 max_tokens 参数却不用，这里必须记 nested 值）
            'num_predict': (payload.get('options') or {}).get('num_predict'),
            'temperature': payload.get('temperature'),
            'has_auth': bool(self.headers.get('Authorization')),
            'auth': self.headers.get('Authorization'),
            'has_tools': bool(payload.get('tools')),
            'n_messages': len(payload.get('messages') or []),
            'stream': payload.get('stream'),
        })

        r = _reply_for(payload.get('model'), payload)
        if r == 'HANG':
            import time
            time.sleep(30)          # 调用方会超时；睡够久即可
            return
        status, body = r
        self._send(status, body)


def start_mock(host='127.0.0.1', port=0):
    """起一个后台假厂商，返回 (server, port)。调用方负责 server.shutdown()。"""
    srv = ThreadingHTTPServer((host, port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, srv.server_address[1]


def seen():
    return list(_SEEN)


def reset():
    _SEEN.clear()


if __name__ == '__main__':
    import sys
    import threading
    s, p = start_mock(port=18099)
    print(f'假厂商已启动 http://127.0.0.1:{p}/v1/chat/completions')
    if '--serve' in sys.argv:
        threading.Event().wait()          # 常驻模式（手动调试用）
    else:
        # ⚠ 默认**不阻塞**：批量门禁会把 scripts/ 下所有 .py 都跑一遍，
        #   常驻会把那一跑拖成超时失败（本文件是测试库，不是门禁）。
        print('（默认不常驻；要常驻加 --serve）')
