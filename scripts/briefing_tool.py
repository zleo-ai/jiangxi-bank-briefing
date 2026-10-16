#!/usr/bin/env python3
"""江西政企研究简报（银行业版）的离线验证、周期计算、关注池管理、内容质量检查与HTML渲染工具。"""
import argparse
import calendar
import csv
import ipaddress
import json
import math
import re
import sys
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

# 渲染时按路径加载模板模块，不在技能安装目录写入__pycache__。
sys.dont_write_bytecode = True

TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
# 不属于Cf但同样不可见、可用来拆分违禁词的字符（组合用字形连接符、韩文填充符、盲文空白、变体选择符）。
_HIDDEN = re.compile('[\u034f\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0\ufe00-\ufe0f\U000e0100-\U000e01ef]')
MAX_BYTES = 256 * 1024
MAX_REGIONS = 30

# 银行业岗位视角：键 → 可读名称。只调整编辑排序、展开深度与跟踪问题，不代表地域范围或机构抬头。
AUDIENCE_LABELS = {
    'regional': '分行综合', 'executive': '行领导', 'corporate': '公司金融', 'strategic_clients': '战略客户',
    'tech_finance': '科技金融', 'green_finance': '绿色金融', 'inclusive': '普惠金融', 'agri_finance': '乡村振兴与涉农金融',
    'institutions': '金融机构与政府平台', 'transaction_banking': '交易银行', 'bills': '票据业务', 'cross_border': '跨境金融',
    'investment_banking': '投行与债券', 'retail': '零售金融', 'retail_credit': '零售信贷', 'private_banking': '私人银行',
    'wealth': '财富管理', 'planning_finance': '计划财务', 'risk': '风险管理', 'credit_approval': '授信审批',
    'compliance': '内控合规', 'operations': '中后台运营', 'relationship': '一线客户经理',
    'custom': '自定义岗位',
}
# 江西省11个设区市；县区只校验所属设区市，名称由执行时核验。
JIANGXI_CITIES = ('南昌市', '景德镇市', '萍乡市', '九江市', '新余市', '鹰潭市', '赣州市', '吉安市', '宜春市', '抚州市', '上饶市')
POOL_TYPES = {'enterprise': '企业', 'project': '项目', 'park': '园区', 'topic': '专题', 'policy': '政策'}
POOL_PRIORITIES = ('high', 'normal', 'low')
MAX_POOL = 300
# 关注池是公开研究范围，不是客户名单：备注和标签中出现这些内部业务字段时拒绝保存。
POOL_PRIVATE = re.compile(r'授信|额度|余额|存款|贷款户|客户经理|内部评级|我行|本行|存量客户|对公账户|账号|账户|联系人|手机|电话')
AUDIENCES = tuple(AUDIENCE_LABELS)
THEMES = {'classic': '暖纸深红', 'navy': '商务蓝', 'ink': '素墨', 'jade': '青绿'}
LAYOUTS = {'standard': '标准版', 'compact': '速读版', 'memo': '要报版'}
COLOR_SCHEMES = ('auto', 'light')
CHART_KINDS = ('publisher', 'theme', 'region', 'enterprise', 'stage')
REPORT_KINDS = ('daily', 'weekly', 'monthly', 'special')
# live_public：当期公开采集；aggregate_only：只汇总已有存档；backfill_public：事后按历史周期公开补采。
COVERAGE_METHODS = ('live_public', 'aggregate_only', 'backfill_public')


class ValidationError(ValueError):
    pass


def fail(path, message):
    raise ValidationError(f'{path}: {message}')


def load_json(path):
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            fail('$', 'JSON不允许非有限数值')
        return result

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                fail('$', 'JSON含重复字段')
            result[key] = value
        return result

    try:
        with open(path, 'rb') as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            fail('$', 'JSON文件超过256KB')
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_float=finite_float,
                          parse_constant=lambda value: fail('$', 'JSON不允许非有限数值'))
    except ValidationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        fail('$', '无法读取有效UTF-8 JSON文件')


def obj(value, path, required, optional=()):
    if type(value) is not dict:
        fail(path, '必须是对象')
    for key in required:
        if key not in value:
            fail(f'{path}.{key}', '缺少必填字段')
    if set(value) - set(required) - set(optional):
        fail(path, '含未声明字段')
    return value


def text(value, path, maximum=200, empty=False):
    if type(value) is not str:
        fail(path, '必须是字符串')
    # Cf含零宽字符与双向覆盖符：会绕过措辞检查或让后续模板文字倒序显示。
    if any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp') or _HIDDEN.match(c) for c in value):
        fail(path, '字符串含控制字符、不可见格式字符或无效Unicode字符')
    value = value.strip()
    if len(value) > maximum or (not empty and not value):
        fail(path, f'字符串长度必须为{0 if empty else 1}至{maximum}')
    return value


def array(value, path, maximum=1000):
    if type(value) is not list:
        fail(path, '必须是数组')
    if len(value) > maximum:
        fail(path, '数组超过数量上限')
    return value


def enum(value, path, allowed):
    if type(value) is not str or value not in allowed:
        fail(path, '不是允许的枚举值')
    return value


def integer(value, path, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        fail(path, f'必须是{minimum}至{maximum}的整数')
    return value


def version(data):
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        fail('$.schema_version', '必须是整数1')


def validate_presentation(value, path):
    """版式偏好：配色、版式、深色模式与图表选择；只影响呈现，不改变事实。"""
    obj(value, path, (), ('theme', 'layout', 'color_scheme', 'charts'))
    result = {}
    if 'theme' in value:
        result['theme'] = enum(value['theme'], path + '.theme', tuple(THEMES))
    if 'layout' in value:
        result['layout'] = enum(value['layout'], path + '.layout', tuple(LAYOUTS))
    if 'color_scheme' in value:
        result['color_scheme'] = enum(value['color_scheme'], path + '.color_scheme', COLOR_SCHEMES)
    if 'charts' in value:
        charts = array(value['charts'], path + '.charts', len(CHART_KINDS))
        result['charts'] = [enum(chart, f'{path}.charts[{i}]', CHART_KINDS) for i, chart in enumerate(charts)]
        if len(set(result['charts'])) != len(result['charts']):
            fail(path + '.charts', '图表类型重复')
    return result


def validate_region(region, p):
    """只接受江西省、11个设区市及其下辖县区。"""
    obj(region, p, ('name', 'parent', 'level'))
    level = enum(region['level'], p + '.level', ('province', 'city', 'county'))
    name, parent = text(region['name'], p + '.name'), text(region['parent'], p + '.parent')
    if level == 'province' and (name, parent) != ('江西省', '中国'):
        fail(p, '本技能只覆盖江西省：省级只能是“江西省”（parent为“中国”）')
    if level == 'city' and (name not in JIANGXI_CITIES or parent != '江西省'):
        fail(p, '设区市必须是江西省11个设区市之一，parent为“江西省”')
    if level == 'county' and parent not in JIANGXI_CITIES:
        fail(p + '.parent', '县区的parent必须是江西省11个设区市之一')
    return {'name': name, 'parent': parent, 'level': level}


def validate_role_profile(value, path):
    obj(value, path, (), ('title', 'focus_directions', 'key_questions', 'deprioritize'))
    result = {}
    if 'title' in value:
        result['title'] = text(value['title'], path + '.title', 40)
    for key, limit, length in (('focus_directions', 12, 40), ('key_questions', 10, 120), ('deprioritize', 10, 40)):
        values = [text(v, f'{path}.{key}[{i}]', length) for i, v in enumerate(array(value.get(key, []), f'{path}.{key}', limit))]
        if len(set(values)) != len(values):
            fail(f'{path}.{key}', '内容重复')
        result[key] = values
    checks = [('title', result.get('title', ''))] + [(f'{key}[{i}]', v) for key in ('focus_directions', 'key_questions', 'deprioritize')
                                                       for i, v in enumerate(result[key])]
    for key, value in checks:
        if POOL_PRIVATE.search(value):
            fail(f'{path}.{key}', '岗位画像不得含授信、余额、账户、联系人等内部客户信息')
    return result


def validate_pool(entries, path):
    """关注池：用户自定义的企业、项目、园区、专题与政策清单，只用于公开研究。"""
    result, ids, names = [], set(), set()
    for i, entry in enumerate(array(entries, path, MAX_POOL)):
        p = f'{path}[{i}]'
        obj(entry, p, ('id', 'name', 'type', 'public_ok'), ('aliases', 'identifier', 'region', 'tags', 'priority', 'note', 'include_subsidiaries'))
        item = {'id': text(entry['id'], p + '.id', 40), 'name': text(entry['name'], p + '.name', 100),
                'type': enum(entry['type'], p + '.type', tuple(POOL_TYPES))}
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', item['id']):
            fail(p + '.id', 'ID只能用小写字母、数字、-和_，1至40位')
        if item['id'] in ids:
            fail(p + '.id', '关注池ID重复')
        ids.add(item['id'])
        if entry['public_ok'] is not True:
            fail(p + '.public_ok', '必须为true：确认该条目可作为公开研究范围检索，不是内部客户名单')
        item['aliases'] = [text(a, f'{p}.aliases[{j}]') for j, a in enumerate(array(entry.get('aliases', []), p + '.aliases', 20))]
        for name in [item['name']] + item['aliases']:
            if name in names:
                fail(p, '名称或别名与其他关注池条目碰撞')
            names.add(name)
        if 'identifier' in entry:
            if item['type'] != 'enterprise':
                fail(p + '.identifier', '只有企业条目可填写统一社会信用代码或证券代码')
            item['identifier'] = text(entry['identifier'], p + '.identifier', 64)
        if 'region' in entry:
            region = text(entry['region'], p + '.region', 40)
            if region != '江西省' and region not in JIANGXI_CITIES and not region.startswith(JIANGXI_CITIES):
                fail(p + '.region', '地区须为江西省、设区市或“设区市+县区”（如“赣州市南康区”）')
            item['region'] = region
        item['tags'] = [text(t, f'{p}.tags[{j}]', 20) for j, t in enumerate(array(entry.get('tags', []), p + '.tags', 10))]
        item['priority'] = enum(entry.get('priority', 'normal'), p + '.priority', POOL_PRIORITIES)
        if 'note' in entry:
            item['note'] = text(entry['note'], p + '.note', 200)
        for key, value in [('note', item.get('note', ''))] + [(f'tags[{j}]', t) for j, t in enumerate(item['tags'])]:
            if POOL_PRIVATE.search(value):
                fail(f'{p}.{key}', '关注池不得记录授信、余额、账户、联系人等内部客户信息')
        flag = entry.get('include_subsidiaries', False)
        if type(flag) is not bool or (flag and item['type'] != 'enterprise'):
            fail(p + '.include_subsidiaries', '必须是布尔值，且只适用于企业条目')
        item['include_subsidiaries'] = flag
        result.append(item)
    return result


def validate_config(data):
    obj(data, '$', ('schema_version', 'audience'),
        ('config_name', 'regions', 'role_profile', 'watch_pool', 'topics', 'scope_mode', 'timezone', 'history_mode', 'max_items',
         'source_types', 'require_topic_match', 'presentation'))
    version(data)
    result = {'schema_version': 1, 'audience': enum(data['audience'], '$.audience', AUDIENCES)}
    if 'config_name' in data:
        result['config_name'] = text(data['config_name'], '$.config_name', 80)
    regions = array(data.get('regions', [{'name': '江西省', 'parent': '中国', 'level': 'province'}]), '$.regions', MAX_REGIONS)
    if not regions:
        fail('$.regions', '至少需要一个区域；不填时默认江西省全省')
    result['regions'], seen = [], set()
    for i, region in enumerate(regions):
        normalized = validate_region(region, f'$.regions[{i}]')
        key = tuple(normalized.values())
        if key in seen:
            fail(f'$.regions[{i}]', '区域重复')
        seen.add(key)
        result['regions'].append(normalized)
    result['role_profile'] = validate_role_profile(data.get('role_profile', {}), '$.role_profile')
    if result['audience'] == 'custom' and 'title' not in result['role_profile']:
        fail('$.role_profile.title', '自定义岗位必须填写岗位名称')
    result['watch_pool'] = validate_pool(data.get('watch_pool', []), '$.watch_pool')
    result['topics'] = [text(v, f'$.topics[{i}]') for i, v in enumerate(array(data.get('topics', []), '$.topics', 30))]
    if len(set(result['topics'])) != len(result['topics']):
        fail('$.topics', '主题重复')
    for field, default, allowed in (
        ('scope_mode', 'any', ('any', 'all')),
        ('timezone', 'Asia/Shanghai', ('Asia/Shanghai',)),
        ('history_mode', 'aggregate_only', ('aggregate_only', 'backfill_public'))):
        result[field] = enum(data.get(field, default), '$.' + field, allowed)
    result['max_items'] = integer(data.get('max_items', 12), '$.max_items', 1, 50)
    source_types = array(data.get('source_types', ['gov', 'enterprise', 'other']), '$.source_types', 3)
    if not source_types:
        fail('$.source_types', '至少选择一种发布主体类别')
    result['source_types'] = [enum(value, f'$.source_types[{i}]', ('gov', 'enterprise', 'other')) for i, value in enumerate(source_types)]
    if len(set(result['source_types'])) != len(result['source_types']):
        fail('$.source_types', '类别重复')
    require_topic = data.get('require_topic_match', False)
    if type(require_topic) is not bool:
        fail('$.require_topic_match', '必须是布尔值')
    if require_topic and not result['topics']:
        fail('$.topics', '只看选定主题时至少需要一个主题')
    result['require_topic_match'] = require_topic
    if 'presentation' in data:
        result['presentation'] = validate_presentation(data['presentation'], '$.presentation')
    return result


def normalize_fraction(value):
    """把1–6位小数秒补齐为6位，使Python 3.10与3.11+解析结果一致。"""
    return re.sub(r'(?<=:\d{2})\.(\d{1,6})', lambda m: '.' + m.group(1).ljust(6, '0'), value, count=1)


def iso_datetime(value, path):
    if text(value, path, 80) != value:
        fail(path, '日期时间不得含首尾空白')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        fail(path, '必须是带时区的ISO日期时间（含秒）')
    if value[-1] != 'Z' and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        fail(path, '无效时区偏移')
    try:
        parsed = datetime.fromisoformat(normalize_fraction(value).replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            fail(path, '缺少时区')
        return parsed.astimezone(TZ)
    except (ValueError, OverflowError):
        fail(path, '无效日期时间')


def iso_date(value, path):
    if text(value, path, 10) != value:
        fail(path, '日期不得含首尾空白')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        fail(path, '必须是YYYY-MM-DD日期')
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail(path, '无效日期')


def compute_window(kind, as_of, period=None):
    enum(kind, '$.kind', REPORT_KINDS)
    now = iso_datetime(as_of, '$.as_of')
    today = now.date()
    try:
        if kind == 'daily':
            start_day = iso_date(period, '$.period') if period is not None else today - timedelta(days=1)
            end_day = start_day + timedelta(days=1)
        elif kind == 'weekly':
            if period is None:
                start_day = today - timedelta(days=today.weekday() + 7)
            else:
                if type(period) is not str or not re.fullmatch(r'\d{4}-W\d{2}', period):
                    fail('$.period', '周周期必须是YYYY-Www')
                start_day = date.fromisocalendar(int(period[:4]), int(period[6:]), 1)
            end_day = start_day + timedelta(days=7)
        elif kind == 'monthly':
            if period is None:
                start_day = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            else:
                if type(period) is not str or not re.fullmatch(r'\d{4}-\d{2}', period):
                    fail('$.period', '月周期必须是YYYY-MM')
                start_day = date(int(period[:4]), int(period[5:]), 1)
            end_day = start_day + timedelta(days=calendar.monthrange(start_day.year, start_day.month)[1])
        else:
            if type(period) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}', period):
                fail('$.period', '专题周期必须是YYYY-MM-DD/YYYY-MM-DD（含首尾两日）')
            first, last = period.split('/')
            start_day, last_day = iso_date(first, '$.period'), iso_date(last, '$.period')
            if last_day < start_day:
                fail('$.period', '专题周期结束日不能早于开始日')
            end_day = last_day + timedelta(days=1)
            if (end_day - start_day).days > 366:
                fail('$.period', '专题周期不超过366个自然日')
        start = datetime.combine(start_day, time(), TZ)
        end = datetime.combine(end_day, time(), TZ)
    except ValidationError:
        raise
    except (ValueError, OverflowError):
        fail('$.period', '无效周期或日期超出范围')
    if start >= now:
        fail('$.period', '不允许未来或零长度周期')
    if kind == 'special' and end > now and now.timetz().replace(tzinfo=None) == time():
        fail('$.as_of', '进行中的专题截止时点不能恰为00:00，否则无法与已完结专题区分；请使用实际运行时点')
    # 只输出可直接写入04-final.json period的三个字段；时区固定为北京时间。
    return {'start': start.isoformat(), 'end_exclusive': min(end, now).isoformat(), 'cutoff': now.isoformat()}


def safe_url(value, path):
    if text(value, path, 4096) != value or any(c.isspace() for c in value) or '\\' in value:
        fail(path, '链接含不安全字符')
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        credential_keys = {'token', 'access_token', 'api_key', 'apikey', 'key', 'authorization',
                           'signature', 'sig', 'credential', 'password', 'secret',
                           'x-amz-signature', 'x-amz-credential', 'x-amz-security-token',
                           'x-goog-signature', 'x-goog-credential', 'session_token', 'auth', 'client_secret'}
        for component in (parts.query, parts.fragment, parts.fragment.replace('?', '&')):
            for key, unused in parse_qsl(component, keep_blank_values=True):
                if key.lower() in credential_keys:
                    fail(path, '不允许明显含凭据或签名的链接')
        if parts.scheme not in ('http', 'https') or not host or parts.username is not None or parts.password is not None:
            fail(path, '仅允许无凭据的HTTP/HTTPS来源链接')
        host = host.rstrip('.').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan')) or '.' not in host and ':' not in host:
            fail(path, '不允许本机或内网地址')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if ':' in host or not re.fullmatch(r'[a-z0-9.-]+', host) or all(re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)', part) for part in host.split('.')):
                fail(path, '不允许非规范主机地址')
            labels = host.split('.')
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in labels):
                fail(path, '无效主机名')
        else:
            if not address.is_global or (getattr(address, 'ipv4_mapped', None) is not None and not address.ipv4_mapped.is_global):
                fail(path, '不允许私网或保留IP地址')
        if port is not None and not 1 <= port <= 65535:
            fail(path, '无效端口')
    except ValueError as exc:
        if isinstance(exc, ValidationError):
            raise
        fail(path, '无效来源链接')
    return value


def evidence_refs(value, path, ids, required):
    refs = array(value, path, 1000)
    if required and not refs:
        fail(path, '必须引用本期已核实事项')
    seen = set()
    for i, ref in enumerate(refs):
        ref = text(ref, f'{path}[{i}]', 500)
        if ref not in ids or ref in seen:
            fail(path, '引用不存在或重复的事项ID')
        seen.add(ref)
    return refs


def validate_report(data):
    obj(data, '$', ('schema_version', 'kind', 'title', 'status', 'audience', 'config_summary', 'period', 'coverage', 'items'),
        ('sections', 'summary', 'issue', 'presentation', 'key_figures', 'watchlist', 'focus', 'pool'))
    version(data)
    if 'focus' in data:
        focus = obj(data['focus'], '$.focus', (), ('title', 'directions'))
        if 'title' in focus:
            text(focus['title'], '$.focus.title', 40)
        for i, value in enumerate(array(focus.get('directions', []), '$.focus.directions', 12)):
            text(value, f'$.focus.directions[{i}]', 40)
    pool_ids = set()
    for i, entry in enumerate(array(data.get('pool', []), '$.pool', MAX_POOL)):
        p = f'$.pool[{i}]'
        obj(entry, p, ('id', 'name', 'type'), ('priority',))
        pid = text(entry['id'], p + '.id', 40)
        if pid in pool_ids:
            fail(p + '.id', '关注池ID重复')
        pool_ids.add(pid)
        text(entry['name'], p + '.name', 100)
        enum(entry['type'], p + '.type', tuple(POOL_TYPES))
        enum(entry.get('priority', 'normal'), p + '.priority', POOL_PRIORITIES)
    kind = enum(data['kind'], '$.kind', REPORT_KINDS)
    status = enum(data['status'], '$.status', ('formal', 'observation'))
    enum(data['audience'], '$.audience', AUDIENCES)
    for field in ('title', 'config_summary'):
        text(data[field], '$.' + field, 20000)
    if 'issue' in data:
        text(data['issue'], '$.issue', 30)
    if 'presentation' in data:
        validate_presentation(data['presentation'], '$.presentation')
    period = obj(data['period'], '$.period', ('start', 'end_exclusive', 'cutoff'))
    dates = {key: iso_datetime(value, '$.period.' + key) for key, value in period.items()}
    start, end, cutoff = (dates[key] for key in ('start', 'end_exclusive', 'cutoff'))
    if not start < end <= cutoff:
        fail('$.period', '必须满足start < end_exclusive <= cutoff')
    if start.timetz().replace(tzinfo=None) != time():
        fail('$.period.start', '周期必须从北京时间00:00开始')
    try:
        if kind == 'daily':
            full_end = start + timedelta(days=1)
        elif kind == 'weekly':
            if start.weekday() != 0:
                fail('$.period.start', '周报必须从周一开始')
            full_end = start + timedelta(days=7)
        elif kind == 'monthly':
            if start.day != 1:
                fail('$.period.start', '月报必须从每月1日开始')
            full_end = start + timedelta(days=calendar.monthrange(start.year, start.month)[1])
        else:
            full_end = end
    except ValidationError:
        raise
    except (ValueError, OverflowError):
        fail('$.period', '周期超出日期范围')
    if end > full_end:
        fail('$.period.end_exclusive', '超出所选报告类型的周期')
    # 专题以结束日次日00:00为完整终点；不在00:00结束表示截至发稿的进行中周期，同样按部分周期处理。
    partial = end < full_end or (kind == 'special' and end.timetz().replace(tzinfo=None) != time())
    if partial and (end != cutoff or status != 'observation'):
        fail('$.period', '部分周期必须截至cutoff并标为观察稿')
    actual_days = (end.date() - start.date()).days + (end.timetz().replace(tzinfo=None) != time())
    if actual_days > 366:
        fail('$.period', '专题周期不超过366个自然日')
    coverage = obj(data['coverage'], '$.coverage', ('expected_days', 'available_days', 'gaps', 'method'), ('highlights',))
    if 'highlights' in coverage:
        for i, value in enumerate(array(coverage['highlights'], '$.coverage.highlights', 3)):
            text(value, f'$.coverage.highlights[{i}]', 500)
    expected = integer(coverage['expected_days'], '$.coverage.expected_days', 1, 366)
    if expected != actual_days:
        fail('$.coverage.expected_days', '必须等于发布周期覆盖的自然日数量')
    available = integer(coverage['available_days'], '$.coverage.available_days', 0, expected)
    gaps = array(coverage['gaps'], '$.coverage.gaps', 366)
    if partial and not gaps:
        fail('$.coverage.gaps', '部分周期必须说明未完成的周期与覆盖局限')
    for i, gap in enumerate(gaps):
        text(gap, f'$.coverage.gaps[{i}]', 2000)
    enum(coverage['method'], '$.coverage.method', COVERAGE_METHODS)
    if status == 'formal' and (gaps or available != expected):
        fail('$.coverage', '正式简报必须覆盖完整且无缺口')
    items = array(data['items'], '$.items', 1000)
    if status == 'formal' and not items:
        fail('$.items', '正式简报至少需要一条已核实事项')
    ids, events = set(), set()
    for i, item in enumerate(items):
        p = f'$.items[{i}]'
        obj(item, p, ('id', 'title', 'summary', 'region', 'enterprise', 'topic', 'published_at', 'event_date', 'sources', 'verification', 'analysis', 'section', 'event_key'),
            ('theme', 'stage', 'publisher_type', 'reader_summary', 'reader_analysis', 'priority', 'pool_ids'))
        refs = array(item.get('pool_ids', []), p + '.pool_ids', 20)
        if len(set(refs)) != len(refs) or any(type(ref) is not str or ref not in pool_ids for ref in refs):
            fail(p + '.pool_ids', '引用了报告pool中不存在或重复的关注池ID')
        if 'reader_summary' in item:
            text(item['reader_summary'], p + '.reader_summary', 20000)
        if 'reader_analysis' in item:
            text(item['reader_analysis'], p + '.reader_analysis', 20000, empty=True)
        if 'publisher_type' in item:
            enum(item['publisher_type'], p + '.publisher_type', ('gov', 'enterprise', 'other'))
        if 'priority' in item:
            enum(item['priority'], p + '.priority', ('high', 'normal'))
        for key in ('theme', 'stage'):
            if key in item:
                text(item[key], p + '.' + key, 80)
        for key in ('id', 'title', 'summary', 'region', 'enterprise', 'topic', 'analysis', 'section', 'event_key'):
            text(item[key], p + '.' + key, 20000 if key in ('summary', 'analysis') else 500, key in ('enterprise', 'analysis'))
        item_id = item['id'].strip()
        event = item['event_key'].strip()
        if item_id in ids:
            fail(p + '.id', '事项ID重复')
        if event in events:
            fail(p + '.event_key', '事件重复')
        ids.add(item_id)
        events.add(event)
        enum(item['verification'], p + '.verification', ('primary_verified', 'independent_verified'))
        published = iso_datetime(item['published_at'], p + '.published_at')
        if not start <= published < end or published > cutoff:
            fail(p + '.published_at', '事项发布日期不在周期或晚于截止时间')
        if item['event_date'] is not None:
            event_day = iso_date(item['event_date'], p + '.event_date')
            if event_day > cutoff.date():
                fail(p + '.event_date', '事件日期晚于截止时间')
        sources = array(item['sources'], p + '.sources', 100)
        if not sources:
            fail(p + '.sources', '至少需要一个来源')
        source_urls = set()
        matches_publication = False
        for j, source in enumerate(sources):
            q = f'{p}.sources[{j}]'
            obj(source, q, ('name', 'url', 'published_at', 'retrieved_at'))
            text(source['name'], q + '.name', 500)
            source_urls.add(safe_url(source['url'], q + '.url'))
            source_date = iso_datetime(source['published_at'], q + '.published_at')
            matches_publication = matches_publication or source_date == published
            retrieved = iso_datetime(source['retrieved_at'], q + '.retrieved_at')
            if not source_date <= retrieved <= cutoff:
                fail(q, '必须满足来源发布日期 <= 检索日期 <= 截止时间')
        if not matches_publication:
            fail(p + '.published_at', '至少一个来源的原始发布日期必须等于事项发布日期')
        if item['verification'] == 'independent_verified' and len(source_urls) < 2:
            fail(p + '.sources', '独立核实至少需要两个不同来源URL，实际独立性仍须人工核验')
    if 'summary' in data:
        summary = obj(data['summary'], '$.summary', ('text', 'evidence_ids'))
        text(summary['text'], '$.summary.text', 400)
        if not items:
            fail('$.summary.evidence_ids', '内容摘要必须引用本期已核实事项，无事项时省略summary')
        evidence_refs(summary['evidence_ids'], '$.summary.evidence_ids', ids, True)
    if 'key_figures' in data:
        figures = array(data['key_figures'], '$.key_figures', 6)
        if figures and not items:
            fail('$.key_figures', '关键数字必须引用本期已核实事项，无事项时省略')
        for i, figure in enumerate(figures):
            p = f'$.key_figures[{i}]'
            obj(figure, p, ('label', 'value', 'note', 'evidence_ids'))
            text(figure['label'], p + '.label', 30)
            text(figure['value'], p + '.value', 30)
            text(figure['note'], p + '.note', 200)
            evidence_refs(figure['evidence_ids'], p + '.evidence_ids', ids, True)
    if 'watchlist' in data:
        for i, entry in enumerate(array(data['watchlist'], '$.watchlist', 12)):
            p = f'$.watchlist[{i}]'
            obj(entry, p, ('text',), ('evidence_ids',))
            text(entry['text'], p + '.text', 300)
            evidence_refs(entry.get('evidence_ids', []), p + '.evidence_ids', ids, False)
    sections = array(data.get('sections', []), '$.sections', 100)
    for i, section in enumerate(sections):
        p = f'$.sections[{i}]'
        obj(section, p, ('heading', 'body', 'evidence_ids'))
        text(section['heading'], p + '.heading', 500)
        text(section['body'], p + '.body', 20000)
        evidence = array(section['evidence_ids'], p + '.evidence_ids', 1000)
        if status == 'formal' and not evidence:
            fail(p + '.evidence_ids', '正式专题必须引用证据')
        for j, evidence_id in enumerate(evidence):
            evidence_id = text(evidence_id, f'{p}.evidence_ids[{j}]', 500)
            if evidence_id not in ids:
                fail(p + '.evidence_ids', '引用了不存在的事项ID')
    return data


# ---------------------------------------------------------------------------
# 内容质量检查：只做可机器判断的写作规范提示，不判断真实性、独立性或主题语义。

# 只匹配“本期收录N条”“政府资讯N条”这类自指计数；“政府发布12条措施”“本月3条新规施行”等不算。
_CJK = '[^\u4e00-\u9fff]'
_COUNT_TAIL = r'\d+\s*(?:条|则)(?=$|' + _CJK + r'|事项|信息|资讯|动态|新闻|内容)'
_SELF = r'(?:本期|本周|本月|本次|今日|昨日|本报)(?:简报|周报|月报|日报|晨报|要报|快报|专题)?'
_AGG = r'(?:共计|合计|累计|共有|共)?(?:收录|核实|整理|编制|汇总|精选|包含|涉及)?了?的?'
_NOUN = r'(?:(?:已核实|公开|重点|区域|政企)的?)?(?:事项|信息|资讯|动态|新闻|内容|要闻)'
COUNT_SUMMARY = re.compile(_SELF + _AGG + _NOUN + r'?\s*\d+\s*(?:条|则)(?=$|' + _CJK + r'|' + _NOUN + r'|中(?=[，,、：:]|政府|企业|其他|有|包括))'
                           + r'|(?:本期|本周|本月|本报)(?:简报|周报|月报)?(?:共计|共有|共)?(?:收录|核实|整理|汇总|精选)?\d+\s*项(?=$|' + _CJK + r'|' + _NOUN + r')'
                           + r'|(?:政府|企业|其他)(?:资讯|来源|类)\s*' + _COUNT_TAIL)
PROCESS_NOTES = (r'编码占位', r'占位符', r'证据根', r'(?:抓取|检索)(?:时间|日期|时点|于)', r'(?<![息品据名保])采集(?:时间|日期|时点)', r'列表页?日期',
                 r'爬取', r'采集脚本', r'非承载网站', r'精确分配', r'已标注\s*\d+\s*/\s*\d+', r'retrieved_at', r'placeholder')
# 推断性措辞：任何字段都不允许。
CREDIT_INFERENCE = (r'可授信', r'建议授信', r'授信建议', r'额度建议', r'营销机会', r'营销建议', r'营销切入', r'抢占客户', r'客户经理可',
                    r'建议(?:由)?客户经理', r'建议[^。；，,]{0,8}拜访', r'(?:授信|营销)潜力', r'重点营销(?:对象|客户)')
# 同样的推断，但这些词也常见于事实报道（领导走访企业、满足融资需求的政策），只在分析类文字中报错。
# 贷款需求等词也常见于政策与统计事实（“满足合理的贷款需求”“贷款需求指数”），只在分析类文字中报错。
CREDIT_INFERENCE_ANALYSIS = (r'拜访客户', r'上门拜访', r'贷款需求', r'(?:融资|资金|信贷)需求(?:旺盛|较大|较强)',
                             r'(?:可|可以|建议|宜)(?:重点|优先|择机)?营销', r'业务切入点')
# 可能是公开披露事实（如银政签约“意向授信额度”），只在分析类文字中提醒。
CREDIT_FACT = (r'授信额度', r'放款')
INVESTMENT_ADVICE = (r'建议(?:买入|增持|卖出|减持)', r'推荐买入', r'目标价(?!格(?:保险|补贴|政策|制度|改革|机制))', r'值得投资',
                     r'(?:买入|增持|减持|卖出|推荐|强烈推荐|优于大市|跑赢行业)[”"」]?(?:（[^）]{0,4}）)?评级', r'投资评级', r'逢低(?:布局|买入|吸纳)',
                     r'逢高减持', r'择机建仓', r'(?:建议|可|宜|适当|适度|逢低|考虑|择机)加仓', r'股价有望')
# 稳赚：风险提示、判决中引用的诈骗话术不算（同一段含警惕、提示、宣判等词时跳过）。
_FRAUD_CONTEXT = re.compile(r'警惕|谨防|提示|提醒|诈骗|非法集资|吸收公众存款|处罚|宣判|所谓|话术|虚假宣传')
# 风险提示、处罚事实中常引用的说法，只在分析类文字中报错。
INVESTMENT_ADVICE_ANALYSIS = (r'收益承诺', r'继续加仓')
HYPE = (r'重磅', r'炸裂', r'震撼', r'史上最', r'全面爆发', r'爆发式', r'王炸', r'必将', r'(?<![盈获毛净])利好', r'利空(?!间)', r'狂飙', r'血洗')
_INVISIBLE = re.compile('[\u00ad\u034f\u115f\u1160\u17b4\u17b5\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\u2800\u3164\ufe00-\ufe0f\ufeff\uffa0]')


def lint_report(data):
    """返回内容质量提示：error须改正，warning建议改正，info供编辑参考。"""
    validate_report(data)
    findings = []

    def add(level, path, message):
        findings.append({'level': level, 'path': path, 'message': message})

    def matches(patterns, value):
        for pattern in patterns:
            found = re.search(pattern, value)
            if found:
                yield found.group(0)

    def scan(value, path, process=False, analysis=False, quoted=False):
        """所有会出现在页面上的文字都查推断与渲染措辞；process只查读者正文，analysis查分析类文字；
        quoted用于来源原题：照录原文，推断与建议措辞只提醒不报错。"""
        value = _INVISIBLE.sub('', value or '')
        if not value:
            return
        level = 'warning' if quoted else 'error'
        for word in matches(CREDIT_INFERENCE + (CREDIT_INFERENCE_ANALYSIS if analysis else ()), value):
            add(level, path, f'含“{word}”：不得从公开信息推断贷款需求、授信或营销安排')
        advice = list(matches(INVESTMENT_ADVICE + (INVESTMENT_ADVICE_ANALYSIS if analysis else ()), value))
        if '稳赚' in value and not _FRAUD_CONTEXT.search(value):
            advice.append('稳赚')
        for word in advice:
            add(level, path, f'含“{word}”：不得给出投资建议或收益判断')
        for word in matches(HYPE, value):
            add('warning', path, f'含“{word}”：避免渲染性或带交易含义的措辞')
        if analysis:
            for word in matches(CREDIT_FACT, value):
                add('warning', path, f'分析文字含“{word}”：只可引用原文已披露的口径，不得推断授信或放款安排')
        if process:
            for pattern in PROCESS_NOTES:
                found = re.search(pattern, value)
                if found:
                    add('error', path, f'读者正文含编制说明“{found.group(0)}”：改写reader_summary/reader_analysis，原说明留在summary/analysis')

    def has_process_note(value):
        value = _INVISIBLE.sub('', value)
        return any(re.search(pattern, value) for pattern in PROCESS_NOTES)

    items = data['items']
    kind = data['kind']
    coverage = data['coverage']
    scan(data['title'], '$.title', process=True)
    scan(data.get('issue', ''), '$.issue', process=True)
    scan(data['config_summary'], '$.config_summary')
    for i, value in enumerate(data.get('focus', {}).get('directions', [])):
        scan(value, f'$.focus.directions[{i}]', process=True)
    for i, value in enumerate(coverage.get('highlights', [])):
        scan(value, f'$.coverage.highlights[{i}]')
    for i, value in enumerate(coverage['gaps']):
        scan(value, f'$.coverage.gaps[{i}]')
    summary = data.get('summary')
    if items and not summary:
        add('warning', '$.summary', '有已核实事项但未编制一句话速览')
    if summary:
        value = summary['text'].strip()
        if COUNT_SUMMARY.search(_INVISIBLE.sub('', value)):
            add('error', '$.summary.text', '速览不得以条数或来源构成充当摘要，应写具体主体、变化与状态')
        if len(value) < 40 or len(value) > 160:
            add('warning', '$.summary.text', f'速览{len(value)}字，建议60至140字')
        elif len(value) < 60 or len(value) > 140:
            add('info', '$.summary.text', f'速览{len(value)}字，常规区间为60至140字')
        scan(value, '$.summary.text', process=True)
    titles = {}
    themes = set()
    seen_labels = set()
    for i, item in enumerate(items):
        p = f'$.items[{i}]'
        title = item['title'].strip()
        if len(title) > 40:
            add('warning', p + '.title', f'标题{len(title)}字，建议不超过40字：主体+动作+对象+状态')
        if '!' in title or '！' in title:
            add('warning', p + '.title', '标题不使用感叹号')
        if title in titles:
            add('warning', p + '.title', f'与{titles[title]}标题重复，确认是否同一事件应合并')
        titles.setdefault(title, p)
        scan(title, p + '.title', process=True)
        if item['section'] not in seen_labels:
            seen_labels.add(item['section'])
            scan(item['section'], p + '.section', process=True)
        for key in ('theme', 'stage', 'topic', 'region', 'enterprise'):
            scan(item.get(key, ''), f'{p}.{key}')
        for j, source in enumerate(item['sources']):
            if source['name'] not in seen_labels:
                seen_labels.add(source['name'])
                scan(source['name'], f'{p}.sources[{j}].name', quoted=True)
        missing = [key for key in ('theme', 'stage', 'publisher_type') if key not in item]
        if missing:
            add('warning', p, '缺少分类字段：' + '、'.join(missing) + '；新编报告应逐条填齐')
        themes.add(item.get('theme', '').strip() or '未分类')
        # 采集字段同样出现在折叠核验记录中，因此始终检查推断措辞；只有作为读者正文时才检查编制说明。
        scan(item['summary'], p + '.summary')
        if 'reader_summary' in item:
            scan(item['reader_summary'], p + '.reader_summary', process=True)
        elif has_process_note(item['summary']):
            add('warning', p + '.summary', '采集摘要含技术说明且未编制reader_summary，读者正文会直接显示这些说明')
        elif len(item['summary']) > 300:
            add('info', p + '.summary', '采集摘要较长，建议编制80至200字的reader_summary')
        scan(item['analysis'], p + '.analysis', analysis=True)
        if 'reader_analysis' in item:
            scan(item['reader_analysis'], p + '.reader_analysis', process=True, analysis=True)
        elif has_process_note(item['analysis']):
            add('warning', p + '.analysis', '分析含技术说明且未编制reader_analysis，页面“研究关注”会直接显示这些说明')
        if item['event_date'] and item['event_date'] > iso_datetime(item['published_at'], p + '.published_at').date().isoformat():
            add('info', p + '.event_date', '事件日晚于发布日：确认是否为计划里程碑，计划事项应写入正文并标“计划”')
    if len(items) >= 6 and len(themes) > len(items) * 0.7:
        add('warning', '$.items', f'{len(items)}条事项分为{len(themes)}个主题，主题过碎；建议按主题词表归并')
    for i, section in enumerate(data.get('sections', [])):
        scan(section['heading'], f'$.sections[{i}].heading', process=True)
        scan(section['body'], f'$.sections[{i}].body', process=True, analysis=True)
    for i, figure in enumerate(data.get('key_figures', [])):
        for key in ('label', 'value', 'note'):
            scan(figure[key], f'$.key_figures[{i}].{key}', process=True)
    for i, entry in enumerate(data.get('watchlist', [])):
        scan(entry['text'], f'$.watchlist[{i}].text', process=True, analysis=True)
    if kind in ('weekly', 'monthly', 'special') and items and not data.get('sections'):
        add('warning', '$.sections', '周报、月报和专题简报应有周期信号或专题分析')
    if kind == 'monthly' and len(data.get('sections', [])) > 4:
        add('info', '$.sections', '月报专题通常2至3个，过多会稀释重点')
    if data['status'] == 'observation' and coverage['gaps'] and not coverage.get('highlights'):
        add('info', '$.coverage.highlights', '观察稿建议用highlights浓缩首屏最重要的覆盖限制')
    counts = {level: sum(1 for f in findings if f['level'] == level) for level in ('error', 'warning', 'info')}
    score = max(0, 100 - 15 * counts['error'] - 5 * counts['warning'] - counts['info'])
    return {'score': score, 'errors': counts['error'], 'warnings': counts['warning'], 'infos': counts['info'], 'findings': findings}


def resolve_presentation(data, theme=None, layout=None, color_scheme=None):
    result = {'theme': 'navy', 'layout': 'standard', 'color_scheme': 'auto'}
    result.update(data.get('presentation', {}))
    for key, value, allowed in (('theme', theme, THEMES), ('layout', layout, LAYOUTS), ('color_scheme', color_scheme, COLOR_SCHEMES)):
        if value is not None:
            result[key] = enum(value, '$.presentation.' + key, tuple(allowed))
    return result


def render_report(data, theme=None, layout=None, color_scheme=None):
    """结构校验后交给自包含的统一阅读模板；不推断摘要或新闻事实。"""
    validate_report(data)
    presentation = resolve_presentation(data, theme, layout, color_scheme)
    import importlib.util
    from pathlib import Path
    design_path = Path(__file__).with_name('report_design.py')
    spec = importlib.util.spec_from_file_location('briefing_report_design', design_path)
    design = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(design)
    return design.render_report(data, presentation, AUDIENCE_LABELS[data['audience']])


POOL_COLUMNS = {'id': 'id', '编号': 'id', 'name': 'name', '名称': 'name', 'type': 'type', '类型': 'type',
                'aliases': 'aliases', '别名': 'aliases', 'identifier': 'identifier', '统一社会信用代码': 'identifier', '证券代码': 'identifier',
                'region': 'region', '地区': 'region', 'tags': 'tags', '标签': 'tags', 'priority': 'priority', '优先级': 'priority',
                'note': 'note', '备注': 'note'}
POOL_TYPE_NAMES = {v: k for k, v in POOL_TYPES.items()}
PRIORITY_NAMES = {'高': 'high', '中': 'normal', '一般': 'normal', '低': 'low'}
POOL_TEMPLATE = '编号,名称,类型,别名,统一社会信用代码,地区,标签,优先级,备注\nexample-ent,示例新材料股份有限公司,企业,示例新材;示例股份,,赣州市,新能源;上市公司,高,只跟踪公开公告\nexample-park,示例高新技术产业开发区,园区,,,南昌市,园区,中,\n'


def import_pool(path):
    """把Excel另存的CSV（UTF-8，表头中英文均可）转成关注池；多值用分号分隔。导入即视为用户确认公开研究范围。"""
    try:
        with open(path, encoding='utf-8-sig', newline='') as source:
            raw = source.read(MAX_BYTES + 1)
    except (OSError, UnicodeError):
        fail('$.csv', '无法读取UTF-8编码的CSV；请在Excel中另存为“CSV UTF-8”')
    if len(raw) > MAX_BYTES:
        fail('$.csv', 'CSV超过256KB')
    rows = list(csv.reader(raw.splitlines()))
    if not rows:
        fail('$.csv', 'CSV为空')
    header = [POOL_COLUMNS.get(cell.strip()) for cell in rows[0]]
    if 'name' not in header or 'type' not in header:
        fail('$.csv', '表头至少需要“名称”和“类型”两列')
    entries = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        cells = {key: cell.strip() for key, cell in zip(header, row) if key and cell.strip()}
        entry = {'id': cells.get('id') or f'pool-{len(entries) + 1:03d}', 'name': cells.get('name', ''),
                 'type': POOL_TYPE_NAMES.get(cells.get('type', ''), cells.get('type', '')), 'public_ok': True}
        for key in ('aliases', 'tags'):
            if key in cells:
                entry[key] = [v.strip() for v in re.split('[;；]', cells[key]) if v.strip()]
        for key in ('identifier', 'region', 'note'):
            if key in cells:
                entry[key] = cells[key]
        if 'priority' in cells:
            entry['priority'] = PRIORITY_NAMES.get(cells['priority'], cells['priority'])
        entries.append(entry)
    try:
        return validate_pool(entries, '$.watch_pool')
    except ValidationError as exc:
        message = str(exc)
        found = re.match(r'\$\.watch_pool\[(\d+)\]', message)
        raise ValidationError(f'CSV第{int(found.group(1)) + 2}行 ' + message if found else message) from None


def configure_streams():
    """非UTF-8控制台（如英文Windows）下不因中文报错；JSON输出另用ensure_ascii保持有效。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(errors='backslashreplace')
            except (ValueError, OSError):
                pass


def stdout_is_utf8():
    return (getattr(sys.stdout, 'encoding', '') or '').lower().replace('-', '') in ('utf8', 'utf8sig')


def main(argv=None):
    configure_streams()
    parser = argparse.ArgumentParser(description='公开简报离线辅助工具')
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate-config')
    validate.add_argument('path')
    window = sub.add_parser('window')
    window.add_argument('--kind', required=True, choices=REPORT_KINDS)
    window.add_argument('--as-of', required=True)
    window.add_argument('--period', help='daily: YYYY-MM-DD；weekly: YYYY-Www；monthly: YYYY-MM；special: YYYY-MM-DD/YYYY-MM-DD')
    render = sub.add_parser('render')
    render.add_argument('--data', required=True)
    render.add_argument('--out', required=True)
    render.add_argument('--theme', choices=tuple(THEMES))
    render.add_argument('--layout', choices=tuple(LAYOUTS))
    render.add_argument('--color-scheme', choices=COLOR_SCHEMES)
    lint = sub.add_parser('lint')
    lint.add_argument('--data', required=True)
    lint.add_argument('--strict', action='store_true', help='有warning时也返回1')
    pool_import = sub.add_parser('pool-import', help='把CSV关注池转为JSON，输出到新文件')
    pool_import.add_argument('--csv', required=True)
    pool_import.add_argument('--out', required=True)
    pool_template = sub.add_parser('pool-template', help='写出CSV关注池模板')
    pool_template.add_argument('--out', required=True)
    sub.add_parser('options')
    args = parser.parse_args(argv)
    try:
        if args.command == 'validate-config':
            result = validate_config(load_json(args.path))
        elif args.command == 'window':
            result = compute_window(args.kind, args.as_of, args.period)
        elif args.command in ('pool-import', 'pool-template'):
            if args.command == 'pool-import':
                content = json.dumps({'watch_pool': import_pool(args.csv)}, ensure_ascii=False, indent=2) + '\n'
            else:
                content = '\ufeff' + POOL_TEMPLATE
            try:
                with open(args.out, 'x', encoding='utf-8', newline='') as target:
                    target.write(content)
            except FileExistsError:
                fail('$.out', '输出路径已存在，禁止覆盖')
            except OSError:
                fail('$.out', '无法创建输出文件')
            return 0
        elif args.command == 'options':
            result = {'kinds': list(REPORT_KINDS), 'themes': THEMES, 'layouts': LAYOUTS, 'pool_types': POOL_TYPES, 'cities': list(JIANGXI_CITIES),
                      'color_schemes': list(COLOR_SCHEMES), 'charts': list(CHART_KINDS), 'audiences': AUDIENCE_LABELS}
        elif args.command == 'lint':
            result = lint_report(load_json(args.data))
            print(json.dumps(result, ensure_ascii=not stdout_is_utf8(), indent=2))
            return 1 if result['errors'] or (args.strict and result['warnings']) else 0
        else:
            output = render_report(load_json(args.data), args.theme, args.layout, args.color_scheme)
            try:
                with open(args.out, 'x', encoding='utf-8', newline='\n') as target:
                    target.write(output)
            except FileExistsError:
                fail('$.out', '输出路径已存在，禁止覆盖')
            except OSError:
                fail('$.out', '无法创建输出文件')
            return 0
        print(json.dumps(result, ensure_ascii=not stdout_is_utf8(), indent=2, allow_nan=False))
        return 0
    except (ValidationError, RecursionError) as exc:
        print(str(exc) if isinstance(exc, ValidationError) else '$: JSON嵌套过深', file=sys.stderr)
        return 2
    except Exception:
        # 不输出可能含本机路径的回溯；结构校验已通过但渲染失败时视为工具缺陷。
        print('$: 内部错误，未生成输出；请保留输入文件并反馈', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
