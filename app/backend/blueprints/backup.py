#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xiaoke799
"""
备份管理路由 Blueprint。
提供文件级备份、自动备份、备份历史管理、备份下载和恢复功能。
"""

import datetime
import json
import os
import glob
import hashlib
import shutil
import zipfile
from flask import Blueprint, request, jsonify, send_file, current_app

from utils import get_db, json_body, scrub_secrets_from_settings_rows as _scrub_secrets_from_settings_rows
from user_context import require_admin
from constants import EXPORT_TABLES, BABY_SCOPED_TABLES as _BABY_SCOPED_TABLES
import safety_code

bp = Blueprint("backup", __name__)

# 备份文件存储目录（相对于应用数据目录）
BACKUP_DIR_NAME = "backups"

# 默认保留的备份数量
DEFAULT_KEEP_COUNT = 10

# 上传备份的体积上限：含相册的 zip 可能到几百 MB
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _get_share_dir(name):
    """解析 fnOS data-share 共享目录（文件管理器可见）。

    config/resource 声明 babycare-fpk/backups、babycare-fpk/exports 后，
    安装时系统创建共享目录；运行时优先：
      1) /var/apps/babycare-fpk/share/<name>
      2) TRIM_DATA_SHARE_PATHS 中以 /<name> 结尾的路径
      3) 回退 None（非 fnOS / 未声明时仍走应用私有目录）
    """
    app_name = 'babycare-fpk'
    candidates = [
        os.path.join('/var/apps', app_name, 'share', name),
        os.path.join('/var/apps', app_name, 'share', app_name, name),
    ]
    for p in candidates:
        if os.path.isdir(p):
            return p
    raw = os.environ.get('TRIM_DATA_SHARE_PATHS', '')
    for part in raw.split(':'):
        part = part.strip()
        if part and (part.endswith('/' + name) or part.endswith('\\' + name) or part.endswith(name)):
            if os.path.isdir(part):
                return part
    return None


def _get_backup_dir():
    """获取备份目录：优先 fnOS 共享目录 babycare-fpk/backups（文件管理器可见）"""
    share = _get_share_dir('backups')
    if share:
        return share
    data_dir = current_app.config.get('DATA_DIR', '')
    if not data_dir:
        data_dir = os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    backup_dir = os.path.join(data_dir, BACKUP_DIR_NAME)
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir


def _get_export_share_dir():
    """导出目录：fnOS 共享 babycare-fpk/exports"""
    share = _get_share_dir('exports')
    if share:
        return share
    data_dir = current_app.config.get('DATA_DIR', '') or os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    path = os.path.join(data_dir, 'exports')
    os.makedirs(path, exist_ok=True)
    return path


def _get_db_path():
    """获取数据库文件路径"""
    data_dir = current_app.config.get('DATA_DIR', '')
    if not data_dir:
        data_dir = os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    return os.path.join(data_dir, 'babycare.db')


# 导出表清单 EXPORT_TABLES 与「带 baby_id 的表」_BABY_SCOPED_TABLES 均已集中到
# constants.py —— 与 settings.py 的 /api/export-all 共用同一份，避免两处清单走岔。
# 本文件继续沿用常量原名，下游使用点无需改动。

# 恢复时可写入的表名白名单（防止备份 JSON 里的 table 键被注入任意 SQL）。
# 只允许 EXPORT_TABLES 中已知的业务表，且不允许 babies 表被直接拼接恢复
# （babies 由 restore 逻辑单独处理）。
_RESTORE_ALLOWED_TABLES = {t for t in EXPORT_TABLES if t != 'babies'}

# 备份文件名合法格式，杜绝路径穿越/任意文件读写。
# 手动备份 backup_<时间戳>.zip（旧版 .json 仍可恢复）；自动备份 auto_backup_<时间戳>.zip。
# 两者都要放行——此前只认 backup_ 前缀，导致自动备份
# 既不出现在列表里、也没法恢复/删除，而且清理逻辑也匹配不到它（越攒越多直到占满磁盘）。
import re as _re
_BACKUP_FILENAME_RE = _re.compile(r'^(?:auto_)?backup_[A-Za-z0-9_\-]+\.(json|zip)$')

# 备份文件的两套前缀 + 两种后缀
BACKUP_GLOBS = (
    'backup_*.json', 'auto_backup_*.json',
    'backup_*.zip', 'auto_backup_*.zip',
)


def iter_backup_files(backup_dir, only_auto=None):
    """列出备份目录下的备份文件，按修改时间倒序。

    only_auto=True  → 只列自动备份
    only_auto=False → 只列手动备份
    only_auto=None  → 两者都列（列表接口用这个）
    """
    if only_auto is True:
        patterns = (BACKUP_GLOBS[1],)
    elif only_auto is False:
        patterns = (BACKUP_GLOBS[0],)
    else:
        patterns = BACKUP_GLOBS
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(backup_dir, pattern)))
    return sorted(set(files), key=os.path.getmtime, reverse=True)


def _validate_backup_filename(filename):
    """校验备份文件名，防路径穿越。
    只允许 backup_ / auto_backup_ 前缀 + 字母数字下划线连字符 + .json/.zip 后缀，
    天然拒绝 ..、/、\\、空字节等一切路径技巧。
    非字符串（JSON 里传数字/布尔）直接判非法 —— 否则 re.match 会抛 TypeError。
    """
    if not filename or not isinstance(filename, str):
        return False
    return bool(_BACKUP_FILENAME_RE.match(filename))


def _get_data_dir():
    try:
        data_dir = current_app.config.get('DATA_DIR', '') or ''
    except Exception:
        data_dir = ''
    if not data_dir:
        data_dir = os.environ.get('BABYCARE_DATA_DIR', '/tmp/babycare')
    return data_dir


# 列名合法字符：字母数字下划线，杜绝 SQL 注入
_COL_NAME_RE = _re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# 备份包内媒体相对前缀
_MEDIA_PREFIX = 'files/'


def _collect_media_files(baby_id=None):
    """收集相册/成长照片等磁盘文件 → {包内相对路径: 本机绝对路径}。

    只打包真实存在的文件；相对路径统一用 /（zip 兼容）。
    """
    data_dir = _get_data_dir()
    db = get_db()
    existing_tables = set(
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    )

    # 表 → 路径列
    path_cols = {
        'photos': ('file_path', 'thumbnail_path'),
        'growth_photos': ('photo_path',),
    }
    media = {}
    for table, cols in path_cols.items():
        if table not in existing_tables:
            continue
        for col in cols:
            try:
                if baby_id is not None:
                    rows = db.execute(f'SELECT {col} FROM {table} WHERE baby_id = ?', (baby_id,)).fetchall()
                else:
                    rows = db.execute(f'SELECT {col} FROM {table}').fetchall()
            except Exception:
                continue
            for row in rows:
                rel = (row[col] if col in row.keys() else row[0]) or ''
                rel = str(rel).replace('\\', '/').strip()
                if not rel or '..' in rel or rel.startswith('/'):
                    continue
                abs_path = os.path.join(data_dir, *rel.split('/'))
                if os.path.isfile(abs_path):
                    media[rel] = abs_path
    return media


def _safe_media_relpath(name):
    """校验 zip 内媒体相对路径，拒绝穿越。"""
    if not name or not isinstance(name, str):
        return None
    name = name.replace('\\', '/').strip()
    if not name.startswith(_MEDIA_PREFIX):
        return None
    rel = name[len(_MEDIA_PREFIX):]
    if not rel or rel.startswith('/') or '..' in rel.split('/'):
        return None
    return rel


def _count_backup_tables(all_data):
    """统计备份里各表的行数。

    备份的真实结构是**按宝宝分组**的：
        {"babies": [{"baby": {...}, "records": {"feeding_records": [...]}}]}
    不是顶层平铺的 {表名: [行]}。统计口径必须跟着这个结构走，
    否则清单里全是 0，恢复校验会一直报「不一致」——
    这种错静态检查发现不了，只有真造一份备份跑一遍才暴露。
    """
    counts = {}
    for entry in (all_data or {}).get('babies') or []:
        if not isinstance(entry, dict):
            continue
        for table, rows in (entry.get('records') or {}).items():
            if isinstance(rows, list):
                counts[table] = counts.get(table, 0) + len(rows)
    return counts


def _build_backup_manifest(all_data, media_count, data_json_bytes=None):
    """备份清单：让「这个备份能不能恢复」变成**可验证**的事。

    以前备份文件躺在那儿，从来没人能回答「它到底能不能用」——真到要恢复的
    那天才发现坏了就晚了。清单里记下：
      - tables       各表行数（恢复后逐表比对）
      - data_sha256  data.json 的哈希 —— 光比行数只能发现「少了几行」，
                     改了一个数值但行数不变是发现不了的（那属于更隐蔽的损坏）
      - source_integrity  备份当时源库自身的完整性检查结果
    """
    tables = _count_backup_tables(all_data)
    integrity = 'unknown'
    try:
        db = get_db()
        row = db.execute('PRAGMA integrity_check').fetchone()
        integrity = (row[0] if row else 'unknown') or 'unknown'
    except Exception as e:  # noqa: BLE001
        integrity = f'error: {e}'
    return {
        'format': 2,
        'version': (all_data or {}).get('version'),
        'tables': tables,
        'table_total': sum(tables.values()),
        'baby_count': len((all_data or {}).get('babies') or []),
        'media_count': media_count,
        'source_integrity': integrity,
        'data_sha256': (hashlib.sha256(data_json_bytes).hexdigest()
                        if data_json_bytes else None),
    }


def write_backup_archive(filepath, all_data, baby_id=None):
    """写备份包：data.json + manifest.json + files/ 相册等媒体。返回 media 文件数。"""
    media = _collect_media_files(baby_id)
    all_data = dict(all_data or {})
    all_data['version'] = '2.0'
    all_data['include_media'] = True
    all_data['media_count'] = len(media)

    with zipfile.ZipFile(filepath, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        payload = json.dumps(all_data, ensure_ascii=False, indent=2)
        zf.writestr('data.json', payload)
        zf.writestr('manifest.json',
                    json.dumps(_build_backup_manifest(all_data, len(media),
                                                      payload.encode('utf-8')),
                               ensure_ascii=False, indent=2))
        for rel, abs_path in media.items():
            # zip 内统一用 files/<rel>
            zf.write(abs_path, _MEDIA_PREFIX + rel)
    return len(media)


def inspect_backup_archive(filepath):
    """校验一个备份包「能不能恢复」。返回报告字典，不抛异常。

    三种结论：
      ok          —— 清单与数据一致，可以用
      no_manifest —— 老版本备份没有清单（兼容），只做能做的检查
      broken      —— 损坏或被截断，明确不可用
    """
    report = {
        'status': 'broken',
        'ok': False,
        'message': '',
        'tables_checked': 0,
        'tables_matched': 0,
        'mismatches': [],
        'media_expected': None,
        'media_actual': None,
        'source_integrity': None,
        'baby_count': None,
    }
    try:
        if not filepath.lower().endswith('.zip'):
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            report.update(status='no_manifest', ok=True,
                          message='旧版 JSON 备份，已读取成功（无清单可比对）',
                          baby_count=len(data.get('babies') or []),
                          tables_checked=len([v for v in data.values()
                                              if isinstance(v, list)]))
            report['tables_matched'] = report['tables_checked']
            return report

        with zipfile.ZipFile(filepath, 'r') as zf:
            names = zf.namelist()
            if 'data.json' not in names:
                report['message'] = '备份包里没有 data.json，不是有效的育儿宝备份'
                return report
            bad = zf.testzip()          # CRC 校验，能抓出截断/损坏的成员
            if bad:
                report['message'] = f'备份包内文件已损坏：{bad}'
                return report
            zf_raw = zf.read('data.json')
            data = json.loads(zf_raw.decode('utf-8'))
            media_actual = len([n for n in names
                                if n.startswith(_MEDIA_PREFIX) and not n.endswith('/')])
            manifest = None
            if 'manifest.json' in names:
                try:
                    manifest = json.loads(zf.read('manifest.json').decode('utf-8'))
                except Exception:
                    manifest = None

        actual = _count_backup_tables(data)
        report['baby_count'] = len(data.get('babies') or [])
        report['media_actual'] = media_actual

        if not manifest:
            report.update(status='no_manifest', ok=True,
                          message='这份备份没有清单（生成于旧版本），已确认可以读取',
                          tables_checked=len(actual),
                          tables_matched=len(actual))
            return report

        report['source_integrity'] = manifest.get('source_integrity')
        report['media_expected'] = manifest.get('media_count')
        expected = manifest.get('tables') or {}
        for table, want in expected.items():
            report['tables_checked'] += 1
            got = actual.get(table)
            if got == want:
                report['tables_matched'] += 1
            else:
                report['mismatches'].append(
                    {'table': table, 'expected': want, 'actual': got})
        # 清单里有、实际没有的表也算不一致（备份被截断的典型表现）
        for table, got in actual.items():
            if table not in expected:
                report['mismatches'].append(
                    {'table': table, 'expected': None, 'actual': got})

        if manifest.get('media_count') is not None and \
                manifest.get('media_count') != media_actual:
            report['mismatches'].append({'table': '(相册文件)',
                                         'expected': manifest.get('media_count'),
                                         'actual': media_actual})

        # 内容哈希：行数一样但值被改过（比如某条记录的奶量被写成 999），
        # 光比行数发现不了，这类损坏更隐蔽
        want_hash = manifest.get('data_sha256')
        if want_hash:
            got_hash = hashlib.sha256(zf_raw).hexdigest() if zf_raw else None
            report['data_sha256_matched'] = (got_hash == want_hash)
            if got_hash != want_hash:
                report['mismatches'].append(
                    {'table': '(data.json 内容哈希)', 'expected': '一致', 'actual': '不一致'})
        else:
            report['data_sha256_matched'] = None

        if report['mismatches']:
            report['status'] = 'broken'
            report['ok'] = False
            report['message'] = (
                '备份内容与清单不一致（可能未完整拷贝或已损坏），'
                f'{len(report["mismatches"])} 处对不上，不建议直接恢复')
        else:
            report['status'] = 'ok'
            report['ok'] = True
            report['message'] = (
                f'校验通过：{report["tables_matched"]} 张表行数一致'
                + (f'，相册 {media_actual} 个文件' if media_actual else '')
            )
        return report
    except FileNotFoundError:
        report['message'] = '找不到这个备份文件'
        return report
    except zipfile.BadZipFile:
        report['message'] = '不是有效的备份文件（zip 已损坏）'
        return report
    except Exception as e:  # noqa: BLE001
        report['message'] = f'校验失败：{e}'
        return report


def _load_backup_file(filepath):
    """读备份（.zip 新格式 / .json 旧格式）→ (backup_data, media_iter_or_None)。

    media_iter: 可选，yield (相对路径, zip 成员名)，供恢复时落盘。
    """
    lower = filepath.lower()
    if lower.endswith('.zip'):
        zf = zipfile.ZipFile(filepath, 'r')
        try:
            raw = zf.read('data.json')
            backup_data = json.loads(raw.decode('utf-8'))
        except Exception:
            zf.close()
            raise
        members = [n for n in zf.namelist() if n.startswith(_MEDIA_PREFIX) and not n.endswith('/')]

        def _iter():
            try:
                for name in members:
                    rel = _safe_media_relpath(name)
                    if rel:
                        yield rel, name
            finally:
                zf.close()

        return backup_data, _iter(), zf
    # 旧 JSON
    with open(filepath, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    return backup_data, None, None


def _extract_media_files(media_iter, zf, data_dir):
    """把备份包内媒体写回 DATA_DIR。返回 (ok, fail)。"""
    ok = fail = 0
    if not media_iter:
        return 0, 0
    for rel, name in media_iter:
        dest = os.path.join(data_dir, *rel.split('/'))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(name) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out)
            ok += 1
        except Exception as e:
            fail += 1
            try:
                current_app.logger.error(f'恢复媒体失败 {rel}: {e}')
            except Exception:
                pass
    return ok, fail


def _safe_cols_and_values(row):
    """校验并返回 (cols, values)：
    - 列名必须匹配合法标识符，否则丢弃该列（防注入）
    - 返回可安全拼接的列名列表和参数值列表
    """
    cols = []
    values = []
    for k, v in row.items():
        if _COL_NAME_RE.match(k):
            cols.append(k)
            values.append(v)
    return cols, values


def _collect_all_data(baby_id=None):
    """收集所有数据（用于备份）"""
    db = get_db()

    # 获取实际存在的表
    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )
    
    all_data = {
        'version': '1.0',
        'exported_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        # 不写入数据库绝对路径（备份文件可能被分享，泄露服务器路径）
        'babies': [],
    }
    
    if baby_id:
        babies = db.execute('SELECT * FROM babies WHERE id = ?', (baby_id,)).fetchall()
    else:
        babies = db.execute('SELECT * FROM babies').fetchall() if 'babies' in existing_tables else []
    
    for baby in babies:
        baby_dict = dict(baby)
        bid = baby_dict['id']
        baby_data = {
            'baby': baby_dict,
            'records': {}
        }
        
        for table in EXPORT_TABLES:
            # 每个宝宝的 records 只放「带 baby_id」的表。
            # 全局表（知识库、比价、记账等）统一进下面的 global 段 —— 早期把它们
            # 也塞进每个宝宝的 records，后果是：① 同一份全局数据按宝宝数重复存储，
            # 备份文件白白膨胀；② 恢复时走「按 baby_id 插入」的路径，而全局表没有
            # baby_id 列 → 每行都失败，用户看到一堆「N 条失败」的假警报。
            if (table == 'babies' or table not in existing_tables
                    or table not in _BABY_SCOPED_TABLES):
                continue
            rows = db.execute(f'SELECT * FROM {table} WHERE baby_id = ?', (bid,)).fetchall()
            baby_data['records'][table] = [dict(r) for r in rows]
        
        all_data['babies'].append(baby_data)
    
    # 全局表（不包含 babies）
    all_data['global'] = {}
    for table in EXPORT_TABLES:
        if table == 'babies' or table in _BABY_SCOPED_TABLES or table not in existing_tables:
            continue
        rows = db.execute(f'SELECT * FROM {table}').fetchall()
        items = [dict(r) for r in rows]
        if table == 'app_settings':
            items = _scrub_secrets_from_settings_rows(items)
        all_data['global'][table] = items
    
    return all_data


def _cleanup_old_backups(backup_dir, keep_count, only_auto=None):
    """清理旧备份，只保留最近的 keep_count 个。

    两类备份**分别排队**（调用点都会显式传 only_auto）：
      自动备份只在自动备份里排队 —— 手动备份是用户主动创建的，被定时任务
      按时间挤掉属于数据事故（自动备份每 24 小时一个，很快就会把手动的那几个顶出去）；
      手动备份同理只在自己这一类里排队。
    """
    backups = iter_backup_files(backup_dir, only_auto)
    for old_backup in backups[keep_count:]:
        try:
            os.remove(old_backup)
        except OSError:
            pass


# ==================== API: 创建备份 ====================

@bp.route('/api/backup/create', methods=['POST'])
@require_admin
def create_backup():
    """创建新的备份文件"""
    data = json_body()
    baby_id = data.get('baby_id')  # None / 空 表示全部备份
    note = str(data.get('note') or '')[:200]

    # 指定了宝宝就必须真实存在：早期对不存在的 id 会生成一个「空的成功备份」，
    # 用户以为备份好了，打开文件却发现一条记录都没有
    if baby_id is not None and str(baby_id).strip() != '':
        if not get_db().execute('SELECT 1 FROM babies WHERE id = ?', (baby_id,)).fetchone():
            return jsonify({'success': False, 'message': '指定的宝宝不存在'}), 404
    else:
        baby_id = None

    backup_dir = _get_backup_dir()

    # 生成备份文件名（zip 包：JSON + 相册文件）
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    prefix = f'backup_{timestamp}'
    filename = f'{prefix}.zip'
    filepath = os.path.join(backup_dir, filename)

    # 收集数据
    all_data = _collect_all_data(baby_id)
    all_data['note'] = note
    all_data['backup_type'] = 'manual'

    media_count = write_backup_archive(filepath, all_data, baby_id=baby_id)

    # 清理旧备份
    keep_count = data.get('keep_count', DEFAULT_KEEP_COUNT)
    # keep_count 校验：非整数或 < 1 一律回退默认值，防止传 0 清空全部历史备份
    if not isinstance(keep_count, int) or isinstance(keep_count, bool) or keep_count < 1:
        keep_count = DEFAULT_KEEP_COUNT
    _cleanup_old_backups(backup_dir, keep_count, only_auto=False)

    file_size = os.path.getsize(filepath)

    return jsonify({
        'success': True,
        'message': f'备份创建成功（含 {media_count} 个相册/照片文件）',
        'filename': filename,
        # 不返回服务器绝对路径（信息泄露）
        'size': file_size,
        'size_human': _format_size(file_size),
        'media_count': media_count,
        'created_at': all_data['exported_at'],
    })


# ==================== API: 获取备份列表 ====================

@bp.route('/api/backup/list', methods=['GET'])
@require_admin
def list_backups():
    """获取备份文件列表（手动 + 自动）"""
    backup_dir = _get_backup_dir()
    
    backups = []
    for filepath in iter_backup_files(backup_dir):
        stat = os.stat(filepath)
        backups.append({
            'filename': os.path.basename(filepath),
            'size': stat.st_size,
            'size_human': _format_size(stat.st_size),
            'created_at': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    
    return jsonify({
        'success': True,
        'data': backups,
        'count': len(backups),
        # 不返回服务器备份目录绝对路径（信息泄露）
    })


# ==================== API: 下载备份文件 ====================

@bp.route('/api/backup/download/<filename>', methods=['GET'])
@require_admin
def download_backup(filename):
    """下载备份文件"""
    # 安全检查：白名单格式校验，杜绝目录遍历/任意文件读取
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404
    
    return send_file(
        filepath,
        mimetype='application/zip' if filename.lower().endswith('.zip') else 'application/json',
        as_attachment=True,
        download_name=filename,
    )


# ==================== API: 删除备份 ====================

@bp.route('/api/backup/delete/<filename>', methods=['DELETE'])
@require_admin
def delete_backup(filename):
    """删除指定备份文件"""
    # 安全检查：白名单格式校验，杜绝路径穿越
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)
    
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404
    
    try:
        os.remove(filepath)
        return jsonify({'success': True, 'message': '备份已删除'})
    except OSError as e:
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


def _restore_from_data(db, backup_data, source='restore'):
    """从备份数据恢复（替换式：先清除同名宝宝旧记录再写入，避免重复恢复导致数据翻倍）。

    - babies 列名是 birthday（不是 birth_date），匹配/插入均用正确列名
    - 表名/列名双重白名单校验，防 SQL 注入
    返回 (restored, failed) 字典。
    """
    restored = {}
    failed = {}

    # 获取实际存在的表，避免对不存在的表执行 DELETE/INSERT
    existing_tables = set(
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    )

    def _delete_baby_records(baby_id):
        """删除指定宝宝的全部业务记录（恢复前置清理）"""
        for table in _RESTORE_ALLOWED_TABLES:
            if table in _BABY_SCOPED_TABLES and table in existing_tables:
                db.execute(f'DELETE FROM {table} WHERE baby_id = ?', (baby_id,))

    def _insert_rows(table, rows, baby_id=None):
        count = 0
        fail = 0
        for row in rows:
            row.pop('id', None)
            if baby_id is not None:
                row['baby_id'] = baby_id
            cols, values = _safe_cols_and_values(row)
            if not cols:
                fail += 1
                continue
            try:
                col_sql = ', '.join(cols)
                placeholders = ', '.join(['?' for _ in cols])
                db.execute(f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders})', values)
                count += 1
            except Exception as e:
                fail += 1
                current_app.logger.error(f'{source} {table} 失败: {e}')
        restored[table] = restored.get(table, 0) + count
        if fail:
            failed[table] = failed.get(table, 0) + fail

    for baby_data in backup_data.get('babies', []):
        baby = baby_data.get('baby', {})
        records = baby_data.get('records', {})

        # 检查是否已存在同名同生日的宝宝（列名：birthday）
        existing = db.execute(
            'SELECT id FROM babies WHERE name = ? AND birthday = ?',
            (baby.get('name'), baby.get('birthday'))
        ).fetchone()

        if existing:
            baby_id = existing['id']
            # 替换式恢复：先清除该宝宝旧记录，避免重复恢复数据翻倍
            _delete_baby_records(baby_id)
            # 档案字段也一并还原：name/birthday 是匹配键必然一致，但头像这类字段
            # 会不同 —— 早期恢复完全不碰 babies 行，用户换过头像后一恢复就回不来了
            db.execute('UPDATE babies SET avatar = ? WHERE id = ?',
                       (baby.get('avatar') or '', baby_id))
        else:
            # 创建新宝宝（列名：birthday；avatar 也要带上，否则新机上头像全丢）
            cursor = db.execute(
                'INSERT INTO babies (name, birthday, gender, due_date, avatar) VALUES (?, ?, ?, ?, ?)',
                (baby.get('name', '未命名'), baby.get('birthday', ''),
                 baby.get('gender', 'other'), baby.get('due_date'),
                 baby.get('avatar') or '')
            )
            baby_id = cursor.lastrowid

        # 恢复各表数据
        for table, rows in records.items():
            # 表名白名单校验：拒绝备份文件中出现的任意表名（防 SQL 注入）
            if table not in _RESTORE_ALLOWED_TABLES:
                failed[table] = failed.get(table, 0) + len(rows)
                current_app.logger.warning('%s时拒绝非法表名: %s', source, table)
                continue
            # 旧版备份把全局表也塞在每个宝宝的 records 里，这些由下面的 global 段统一
            # 恢复；这里必须跳过 —— 按 baby_id 插入全局表会因「没有该列」逐行失败，
            # 白白给用户报一堆失败数。
            if table not in _BABY_SCOPED_TABLES:
                continue
            if not rows or table not in existing_tables:
                continue
            _insert_rows(table, rows, baby_id=baby_id)

    # 恢复全局表（不按 baby 关联，如 diaper_prices）：先清空再写入
    for table, rows in (backup_data.get('global') or {}).items():
        if table not in _RESTORE_ALLOWED_TABLES or table in _BABY_SCOPED_TABLES:
            continue
        if not rows or table not in existing_tables:
            continue
        db.execute(f'DELETE FROM {table}')
        _insert_rows(table, rows)

    return restored, failed


# ==================== API: 恢复备份 ====================

@bp.route('/api/backup/restore', methods=['POST'])
@require_admin
def restore_backup():
    """从备份文件恢复数据（危险：覆盖现有记录，必须安全码）"""
    data = json_body()
    filename = str(data.get('filename') or '').strip()
    if not filename:
        return jsonify({'success': False, 'message': '请指定备份文件'}), 400

    # 安全检查：白名单格式校验，杜绝路径穿越
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400

    db = get_db()
    ok, msg = safety_code.verify(db, data.get('safety_code', ''))
    if not ok:
        return jsonify({'success': False, 'message': msg, 'needSafetyCode': True}), 403

    backup_dir = _get_backup_dir()
    filepath = os.path.join(backup_dir, filename)

    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'message': '备份文件不存在'}), 404

    # 恢复前强制 pre-flight 完整性校验：_restore_from_data 对同名宝宝是
    # **先删后插**，损坏备份（拷贝中断 / 磁盘写满 / 被手工改过）只要 JSON
    # 还能解析就会通过，结果是删掉现有记录 + 写入残本后 commit —— 静默数据丢失。
    # inspect_backup_archive（manifest 行数比对 + data.json sha256 + zip CRC）
    # 早就写好了，但三个恢复入口都没调用它。机制在，链路没接上 = 没有。
    report = inspect_backup_archive(filepath)
    if not report.get('ok'):
        return jsonify({
            'success': False,
            'message': f'备份校验未通过，已拒绝恢复：{report.get("message") or "备份可能已损坏"}',
            'inspect': report,
        }), 400

    try:
        backup_data, media_iter, zf = _load_backup_file(filepath)
    except (json.JSONDecodeError, OSError, zipfile.BadZipFile, KeyError) as e:
        return jsonify({'success': False, 'message': f'读取备份失败: {str(e)}'}), 400

    # 验证备份格式
    if 'babies' not in backup_data:
        if zf:
            zf.close()
        return jsonify({'success': False, 'message': '无效的备份格式'}), 400

    restored = {}
    failed = {}
    media_ok = media_fail = 0

    try:
        restored, failed = _restore_from_data(db, backup_data, source='恢复')
        media_ok, media_fail = _extract_media_files(media_iter, zf, _get_data_dir())
        db.commit()

        total_restored = sum(restored.values())
        total_failed = sum(failed.values()) if failed else 0
        msg = f'恢复完成，共恢复 {total_restored} 条记录'
        if total_failed:
            msg += f'，{total_failed} 条失败'
        if media_ok or media_fail:
            msg += f'，相册文件 {media_ok} 个'
            if media_fail:
                msg += f'（{media_fail} 个失败）'

        return jsonify({
            'success': True,
            'message': msg,
            'restored': restored,
            'failed': failed,
            'media_restored': media_ok,
            'media_failed': media_fail,
        })

    except Exception as e:
        db.rollback()
        return jsonify({'success': False, 'message': f'恢复失败: {str(e)}'}), 500
    finally:
        if zf:
            try:
                zf.close()
            except Exception:
                pass


# ==================== API: 上传备份恢复 ====================

@bp.route('/api/backup/upload-restore', methods=['POST'])
@require_admin
def upload_restore():
    """上传备份文件并恢复（JSON / ZIP，必须安全码）"""
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '请选择备份文件'}), 400

    # 安全码：表单字段或 JSON 字段
    code = request.form.get('safety_code') or ''
    if not code:
        try:
            code = (request.get_json(silent=True) or {}).get('safety_code') or ''
        except Exception:
            code = ''
    ok, msg = safety_code.verify(get_db(), code)
    if not ok:
        return jsonify({'success': False, 'message': msg, 'needSafetyCode': True}), 403

    file = request.files['file']
    fname = (file.filename or '').lower()
    if not fname.endswith(('.json', '.zip')):
        return jsonify({'success': False, 'message': '请上传 JSON 或 ZIP 格式的备份文件'}), 400

    # 体积检查：先量大小再解析，别让一个超大包把内存吃光
    stream = file.stream
    try:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
    except (OSError, AttributeError):
        size = 0
    if size > MAX_UPLOAD_BYTES:
        return jsonify({
            'success': False,
            'message': f'备份文件过大（{size // 1024 // 1024}MB，上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB）',
        }), 413

    tmp_path = None
    zf = None
    media_iter = None
    try:
        if fname.endswith('.zip'):
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix='.zip', prefix='babycare_restore_')
            os.close(fd)
            with open(tmp_path, 'wb') as out:
                shutil.copyfileobj(stream, out)
            backup_data, media_iter, zf = _load_backup_file(tmp_path)
            # 上传恢复同样要过 pre-flight（理由同 restore_backup：先删后插）
            report = inspect_backup_archive(tmp_path)
            if not report.get('ok'):
                return jsonify({
                    'success': False,
                    'message': f'备份校验未通过，已拒绝恢复：{report.get("message") or "备份可能已损坏"}',
                    'inspect': report,
                }), 400
        else:
            try:
                backup_data = json.load(stream)
            except json.JSONDecodeError:
                return jsonify({'success': False, 'message': '无效的 JSON 文件'}), 400

        if 'babies' not in backup_data:
            return jsonify({'success': False, 'message': '无效的备份格式'}), 400

        db = get_db()
        restored, failed = _restore_from_data(db, backup_data, source='上传恢复')
        media_ok, media_fail = _extract_media_files(media_iter, zf, _get_data_dir())
        db.commit()

        total_restored = sum(restored.values())
        total_failed = sum(failed.values()) if failed else 0
        msg = f'恢复完成，共恢复 {total_restored} 条记录'
        if total_failed:
            msg += f'，{total_failed} 条失败'
        if media_ok or media_fail:
            msg += f'，相册文件 {media_ok} 个'
        return jsonify({
            'success': True,
            'message': msg,
            'restored': restored,
            'failed': failed,
            'media_restored': media_ok,
        })

    except (json.JSONDecodeError, zipfile.BadZipFile, KeyError, OSError) as e:
        return jsonify({'success': False, 'message': f'读取备份失败: {str(e)}'}), 400
    except Exception as e:
        get_db().rollback()
        return jsonify({'success': False, 'message': f'恢复失败: {str(e)}'}), 500
    finally:
        if zf:
            try:
                zf.close()
            except Exception:
                pass
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _format_size(size):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


# ==================== API: 共享目录导出 / 恢复 ====================

@bp.route('/api/backup/share-info', methods=['GET'])
@require_admin
def share_info():
    """返回 fnOS 共享目录 + 应用中心授权目录状态"""
    try:
        from fnos_open_api import get_shared_accessible_folders
        authorized = get_shared_accessible_folders()
    except Exception:
        authorized = []
    backup_dir = _get_backup_dir()
    export_dir = _get_export_share_dir()
    return jsonify({
        'success': True,
        'data': {
            'backup_dir': backup_dir,
            'export_dir': export_dir,
            'backup_is_share': bool(_get_share_dir('backups')),
            'export_is_share': bool(_get_share_dir('exports')),
            'share_roots': [
                _get_share_dir('backups'),
                _get_share_dir('exports'),
            ],
            # 应用中心「授权目录」：管理员在应用设置里配置的固定目录
            'authorized_paths': authorized,
        },
    })


@bp.route('/api/backup/export-to-share', methods=['POST'])
@require_admin
def export_to_share():
    """把完整备份导出到共享目录（默认 babycare-fpk/exports，文件管理器可见）"""
    data = json_body()
    note = str(data.get('note') or '')[:200]
    # 默认写 exports 共享（用户可见导出）；backups 共享留给备份轮转
    share_dir = _get_export_share_dir()
    # 可选：导出到应用中心「授权目录」中的某个子目录（须是已授权路径）
    target = str(data.get('target_path') or '').strip()
    if target:
        try:
            from fnos_open_api import get_shared_accessible_folders
            allowed = get_shared_accessible_folders()
        except Exception:
            allowed = []
        real_target = os.path.realpath(target)
        if not any(real_target == os.path.realpath(p) or real_target.startswith(os.path.realpath(p) + os.sep)
                   for p in allowed):
            return jsonify({'success': False, 'message': '目标目录不在应用中心授权目录内'}), 403
        share_dir = real_target
        os.makedirs(share_dir, exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'backup_{timestamp}.zip'
    filepath = os.path.join(share_dir, filename)

    all_data = _collect_all_data()
    all_data['note'] = note
    all_data['backup_type'] = 'export-to-share'
    all_data['share_dir'] = os.path.basename(share_dir)

    media_count = 0
    try:
        # 与 create_backup 一致：优先 zip（含相册），失败再退 JSON
        try:
            media_count = write_backup_archive(filepath, all_data)
        except Exception:
            filepath = os.path.join(share_dir, f'backup_{timestamp}.json')
            filename = os.path.basename(filepath)
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({'success': False, 'message': f'导出到共享目录失败: {e}'}), 500

    return jsonify({
        'success': True,
        'message': f'已导出到共享目录（含 {media_count} 个相册/照片文件）',
        'filename': filename,
        'path': filepath,
        'share': os.path.basename(share_dir),
        'media_count': media_count,
        'size': os.path.getsize(filepath),
        'size_human': _format_size(os.path.getsize(filepath)),
    })


@bp.route('/api/backup/share-files', methods=['GET'])
@require_admin
def list_share_files():
    """列出备份文件：共享目录（backups/exports）+ 应用中心授权目录 + 应用私有目录"""
    dirs = []
    for d in (_get_backup_dir(), _get_export_share_dir()):
        if d and os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    try:
        from fnos_open_api import get_shared_accessible_folders
        for p in get_shared_accessible_folders():
            if p and os.path.isdir(p) and os.path.realpath(p) not in [os.path.realpath(x) for x in dirs]:
                dirs.append(p)
    except Exception:
        pass
    items = []
    for d in dirs:
        for fp in iter_backup_files(d):
            st = os.stat(fp)
            items.append({
                'filename': os.path.basename(fp),
                'dir': d,
                'size': st.st_size,
                'size_human': _format_size(st.st_size),
                'created_at': datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            })
    return jsonify({'success': True, 'data': items, 'count': len(items)})


@bp.route('/api/backup/restore-from-share', methods=['POST'])
@require_admin
def restore_from_share():
    """从共享备份目录恢复（必须安全码，仅允许目录内白名单文件名）"""
    data = json_body()
    filename = str(data.get('filename') or '')
    code = str(data.get('safety_code') or '')
    dir_hint = str(data.get('dir') or '').strip()
    ok, msg = safety_code.verify(get_db(), code)
    if not ok:
        return jsonify({'success': False, 'message': msg, 'needSafetyCode': True}), 403
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '非法的备份文件名'}), 400

    search_dirs = []
    for d in (_get_backup_dir(), _get_export_share_dir()):
        if d and os.path.isdir(d) and d not in search_dirs:
            search_dirs.append(d)
    if dir_hint:
        real_hint = os.path.realpath(dir_hint)
        try:
            from fnos_open_api import get_shared_accessible_folders
            allowed = [os.path.realpath(p) for p in get_shared_accessible_folders()]
        except Exception:
            allowed = []
        known = {os.path.realpath(d) for d in search_dirs}
        if real_hint in allowed or real_hint in known:
            search_dirs.insert(0, real_hint)

    filepath = None
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            filepath = candidate
            break
    if not filepath:
        return jsonify({'success': False, 'message': '目录中不存在该备份'}), 404

    # 共享目录里的备份是用户手工拷贝进来的，损坏概率更高 —— pre-flight 更不能省
    report = inspect_backup_archive(filepath)
    if not report.get('ok'):
        return jsonify({
            'success': False,
            'message': f'备份校验未通过，已拒绝恢复：{report.get("message") or "备份可能已损坏"}',
            'inspect': report,
        }), 400

    zf = None
    try:
        backup_data, media_iter, zf = _load_backup_file(filepath)
        if 'babies' not in backup_data:
            return jsonify({'success': False, 'message': '无效的备份格式'}), 400
        db = get_db()
        restored, failed = _restore_from_data(db, backup_data, source='共享目录恢复')
        media_ok, media_fail = _extract_media_files(media_iter, zf, _get_data_dir())
        db.commit()
        total_restored = sum(restored.values())
        total_failed = sum(failed.values()) if failed else 0
        msg = f'恢复完成，共恢复 {total_restored} 条记录'
        if total_failed:
            msg += f'，{total_failed} 条失败'
        return jsonify({
            'success': True,
            'message': msg,
            'restored': restored,
            'failed': failed,
            'media_restored': media_ok,
        })
    except Exception as e:
        get_db().rollback()
        return jsonify({'success': False, 'message': f'恢复失败: {e}'}), 500
    finally:
        if zf:
            try:
                zf.close()
            except Exception:
                pass


@bp.route('/api/backup/inspect/<filename>', methods=['GET'])
@require_admin
def inspect_backup(filename):
    """校验某个备份包「能不能恢复」——只读，不碰数据。

    为什么需要：备份文件躺在那儿不动，从来没人能回答它还能不能用。
    有了这个接口，恢复之前先花一秒验一下，比恢复到一半失败强。
    """
    if not _validate_backup_filename(filename):
        return jsonify({'success': False, 'message': '备份文件名不合法'}), 400
    path = os.path.join(_get_backup_dir(), filename)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'message': '找不到这个备份文件'}), 404
    report = inspect_backup_archive(path)
    return jsonify({'success': True, 'data': report})
