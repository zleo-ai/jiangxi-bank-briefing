"""江西政企研究简报（银行业版）自包含阅读页：四套配色 × 三种版式；输入数据由调用方验证，不推断摘要或新闻事实。"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
import re

TZ8 = timezone(timedelta(hours=8))
WEEKDAYS = '一二三四五六日'
DIGITS = '〇一二三四五六七八九'
PUBLISHER_LABELS = {'gov': '政府资讯', 'enterprise': '企业资讯', 'other': '其他来源'}
PUBLISHER_SLOTS = {'政府资讯': 1, '企业资讯': 2, '其他来源': 3}
SERIES = {'daily': '每日观察', 'weekly': '周度观察', 'monthly': '月度观察', 'special': '专题观察'}
PARTIAL = {'daily': '部分日报', 'weekly': '部分周报', 'monthly': '部分月报', 'special': '专题观察（进行中）'}
MAX_SEGMENTS = 4
METHOD_LABELS = {'live_public': '当期公开采集', 'aggregate_only': '仅汇总已有记录', 'backfill_public': '补采公开历史资料'}
CHARTS = {
    'publisher': ('来源构成', '原始披露主体', ''),
    'theme': ('内容主题', '本期关注主题', ''),
    'region': ('地区分布', '事项所在地区', ''),
    'enterprise': ('企业动态', '涉及企业', ''),
    'stage': ('事项阶段', '事项所处阶段', '各事项阶段独立统计，不构成同一流程的漏斗。'),
}
MUTED = {'theme': '未分类', 'stage': '未标注'}
MAX_BARS = 7
CHECK = '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
DISCLAIMER = '本报告基于公开信息整理，仅供参阅交流，不构成投资建议、授信审批意见或对任何主体的信用保证。信息可能存在披露滞后和检索遗漏，重要事项请核对原始来源。'


def esc(value):
    return escape(str(value), quote=True)


def moment(value):
    # 与briefing_tool.normalize_fraction一致：补齐小数秒，兼容Python 3.10。
    value = re.sub(r'(?<=:\d{2})\.(\d{1,6})', lambda m: '.' + m.group(1).ljust(6, '0'), value, count=1)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(TZ8)


def cn_date(day):
    return f'{day.year}年{day.month}月{day.day}日'


def month_day(day):
    return f'{day.month}月{day.day}日'


def cn_number(n):
    if n < 10:
        return DIGITS[n]
    tens, ones = divmod(n, 10)
    if n < 100:
        return ('' if tens == 1 else DIGITS[tens]) + '十' + (DIGITS[ones] if ones else '')
    return str(n)


def category(item):
    return item['section'].split('·', 1)[0].strip()


def publisher_of(item):
    if 'publisher_type' in item:
        return PUBLISHER_LABELS[item['publisher_type']]
    return category(item)


def links(ids, anchors):
    parts = []
    for value in ids:
        anchor = anchors.get(value.strip())
        if anchor:
            number = anchor.split('-')[-1]
            parts.append('<a href="#' + anchor + '" aria-label="查看关联事项' + number + '">[' + number + ']</a>')
    return ' '.join(parts)


def related(ids, anchors, label='关联事项'):
    found = links(ids, anchors)
    return '<div class="related">' + label + ' ' + found + '</div>' if found else ''


# ---------------------------------------------------------------------------
# 周期与报头

def period_info(data):
    period = data['period']
    start, end, cutoff = (moment(period[key]) for key in ('start', 'end_exclusive', 'cutoff'))
    kind = data['kind']
    if kind == 'daily':
        full_end = start + timedelta(days=1)
    elif kind == 'weekly':
        full_end = start + timedelta(days=7)
    elif kind == 'monthly':
        full_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        full_end = end
    # 专题不在00:00结束即为截至发稿的进行中周期；不做日期加法，避免9999年边界溢出。
    partial = end < full_end or (kind == 'special' and (end.hour, end.minute, end.second, end.microsecond) != (0, 0, 0, 0))
    last = end - timedelta(microseconds=1)
    if kind == 'daily':
        span = cn_date(start) + '发布'
    elif kind == 'weekly':
        year, week, _ = start.isocalendar()
        span = f'{year}年第{week}周（{month_day(start)}—{month_day(last)}）'
    elif kind == 'monthly':
        span = f'{start.year}年{start.month}月'
    else:
        span = cn_date(start) + '—' + (month_day(last) if last.year == start.year else cn_date(last))
    if partial:
        span += f'，截至{month_day(cutoff)} {cutoff:%H:%M}'
    series = PARTIAL[kind] if partial else SERIES[kind]
    report_day = cn_date(cutoff) + ' 星期' + WEEKDAYS[cutoff.weekday()]
    return {'series': series, 'span': span, 'report_day': report_day, 'cutoff': cutoff, 'partial': partial}


def masthead(data, info):
    title = data['title'].split('｜', 1)
    status = '正式简报' if data['status'] == 'formal' else '观察稿'
    parts = ['<header class="mast"><p class="series"><span>公开资讯 · ' + esc(info['series']) + '</span>']
    if data.get('issue'):
        parts.append('<span class="issue">' + esc(data['issue']) + '</span>')
    parts.append('</p><h1>' + esc(title[0].strip()) + '</h1>')
    if len(title) > 1 and not any(char.isdigit() for char in title[1]):
        parts.append('<p class="subtitle">' + esc(title[1].strip()) + '</p>')
    parts.append('<div class="dateline"><span>' + esc(info['report_day']) + '</span><span>覆盖 ' + esc(info['span']) + '</span>'
                 '<span class="status ' + esc(data['status']) + '">' + status + '</span>'
                 '<span>截止 ' + esc(info['cutoff'].strftime('%m-%d %H:%M')) + ' · 北京时间</span></div></header>')
    return ''.join(parts)


def scope_and_notices(data):
    coverage = data['coverage']
    parts = ['<section class="scope" aria-label="实际编制范围"><p><b>编制范围</b><span>' + esc(data['config_summary']) + '</span></p>']
    directions = data.get('focus', {}).get('directions', [])
    if directions:
        parts.append('<p><b>关注方向</b><span>' + '、'.join(esc(value) for value in directions) + '</span></p>')
    parts.append('</section>')
    notes = coverage.get('highlights') or coverage['gaps'][:2]
    if notes:
        parts.append('<section class="coverage-gaps notice" aria-label="本期阅读提示"><b>阅读提示</b><ul>'
                     + ''.join('<li>' + esc(note) + '</li>' for note in notes) + '</ul></section>')
    return ''.join(parts)


# ---------------------------------------------------------------------------
# 图表：只表达本期合格样本；HTML/CSS绘制，无脚本，每张图附数据表

def tallies(items):
    result = {key: Counter() for key in CHARTS}
    for item in items:
        result['publisher'][publisher_of(item)] += 1
        result['theme'][item.get('theme', '').strip() or '未分类'] += 1
        result['region'][item['region'].strip()] += 1
        if item['enterprise'].strip():
            result['enterprise'][item['enterprise'].strip()] += 1
        result['stage'][item.get('stage', '').strip() or '未标注'] += 1
    return result


def shares(values):
    total = sum(values)
    raw = [value * 100 / total for value in values]
    floors = [int(value) for value in raw]
    order = sorted(range(len(values)), key=lambda i: (floors[i] - raw[i], i))
    for i in order[:100 - sum(floors)]:
        floors[i] += 1
    return floors


def ranked(counter, muted=None):
    rows = [(name, value) for name, value in counter.most_common() if name != muted]
    tail = [(muted, counter[muted])] if muted and counter.get(muted) else []
    return rows, tail


def folded(counter, muted=None):
    """返回(名称, 数量, 类型)：row为普通类别，rest为折叠合计（不画条、不参与比例尺），muted为未分类。"""
    rows, tail = ranked(counter, muted)
    limit = MAX_BARS - len(tail)
    result = [(name, value, 'row') for name, value in rows]
    if len(rows) > limit:
        rest = rows[limit - 1:]
        result = result[:limit - 1] + [(f'其余{len(rest)}项合计', sum(value for _, value in rest), 'rest')]
    return result + [(name, value, 'muted') for name, value in tail]


def publisher_rows(counter):
    """三种原始披露主体固定三色；旧档的其他分区类别一律中性灰，超过上限时折叠为“其余N类”。"""
    rows = [(name, counter[name], PUBLISHER_SLOTS[name]) for name in PUBLISHER_SLOTS if name in counter]
    rows += [(name, value, 0) for name, value in counter.most_common() if name not in PUBLISHER_SLOTS]
    if len(rows) > MAX_SEGMENTS:
        rest = rows[MAX_SEGMENTS - 1:]
        rows = rows[:MAX_SEGMENTS - 1] + [(f'其余{len(rest)}类', sum(value for _, value, _ in rest), 0)]
    return rows


def caption(key):
    kicker, title, caveat = CHARTS[key]
    html = '<figcaption><span class="kicker">' + kicker + '</span><h3>' + title + '<span class="unit">条</span></h3></figcaption>'
    if caveat:
        html += '<p class="chart-caveat">' + caveat + '</p>'
    return html


def stack_chart(counter):
    rows = publisher_rows(counter)
    percents = shares([value for _, value, _ in rows])
    label = '原始披露主体：' + '；'.join(f'{name}{value}条（{pct}%）' for (name, value, _), pct in zip(rows, percents))
    parts = ['<figure class="chart chart-stack" aria-label="' + esc(label) + '">', caption('publisher'), '<div class="stack" aria-hidden="true">']
    for (name, value, slot), pct in zip(rows, percents):
        parts.append('<span class="seg s' + str(slot) + '" style="flex-grow:' + str(value) + '" title="' + esc(f'{name}：{value}条（{pct}%）') + '"></span>')
    parts.append('</div><ul class="legend">')
    for (name, value, slot), pct in zip(rows, percents):
        parts.append('<li><i class="sw s' + str(slot) + '"></i><span>' + esc(name) + '</span><b>' + str(value) + '</b><em>' + str(pct) + '%</em></li>')
    parts.append('</ul></figure>')
    return ''.join(parts)


def bar_chart(key, counter):
    rows = folded(counter, MUTED.get(key))
    top = max(value for _, value, kind in rows if kind != 'rest')
    label = CHARTS[key][1] + '：' + '；'.join(f'{name}{value}条' for name, value, _ in rows)
    parts = ['<figure class="chart chart-bars" aria-label="' + esc(label) + '">', caption(key), '<ol class="bars">']
    for name, value, kind in rows:
        fill = '' if kind == 'rest' else '<span class="bar-fill" style="width:' + f'{value / top * 100:.2f}' + '%"></span>'
        parts.append('<li' + ('' if kind == 'row' else ' class="' + ('muted rest' if kind == 'rest' else 'muted') + '"') + ' title="'
                     + esc(f'{name}：{value}条') + '"><span class="bar-label">' + esc(name) + '</span><span class="bar-track">' + fill
                     + '</span><b class="bar-value">' + str(value) + '</b></li>')
    parts.append('</ol></figure>')
    return ''.join(parts)


def chart_table(key, counter):
    if key == 'publisher':
        rows = [(name, counter[name]) for name in PUBLISHER_SLOTS if name in counter]
        rows += [(name, value) for name, value in counter.most_common() if name not in PUBLISHER_SLOTS]
    else:
        main, tail = ranked(counter, MUTED.get(key))
        rows = main + tail
    percents = shares([value for _, value in rows])
    body = ''.join('<tr><th scope="row">' + esc(name) + '</th><td>' + str(value) + '</td><td>' + str(pct) + '%</td></tr>'
                   for (name, value), pct in zip(rows, percents))
    return ('<table><caption>' + CHARTS[key][1] + '</caption><thead><tr><th scope="col">类别</th><th scope="col">事项</th>'
            '<th scope="col">占比</th></tr></thead><tbody>' + body + '</tbody></table>')


def chosen_charts(data, presentation, counts):
    requested = presentation.get('charts')
    if requested is None:
        layout = presentation['layout']
        if layout == 'memo':
            requested = []
        elif layout == 'compact':
            requested = ['publisher']
        elif data['kind'] == 'daily':
            requested = ['publisher', 'theme']
        else:
            requested = ['publisher', 'theme', 'region', 'enterprise']
    total = len(data['items'])
    return [key for key in requested if len(counts[key]) >= 2 and (key == 'publisher' or total >= 3)]


def stats(data, counts):
    items, coverage = data['items'], data['coverage']
    primary = sum(1 for item in items if item['verification'] == 'primary_verified')
    themes = counts['theme']
    classified = len([name for name in themes if name != '未分类'])
    tiles = [
        ('已核实事项', str(len(items)), '条', ''),
        ('一手来源核实', str(primary), '条', f'其余{len(items) - primary}条为独立来源互证' if primary < len(items) else ''),
        ('内容主题', str(classified), '类', f'含未分类{themes["未分类"]}条' if themes.get('未分类') else ''),
        ('日期覆盖', f'{coverage["available_days"]}/{coverage["expected_days"]}', '天', ''),
    ]
    return '<dl class="stats">' + ''.join(
        '<div><dt>' + label + '</dt><dd>' + value + '<small>' + unit + '</small></dd>' + ('<dd class="stat-note">' + esc(note) + '</dd>' if note else '') + '</div>'
        for label, value, unit, note in tiles) + '</dl>'


def dashboard(data, presentation, counts):
    keys = chosen_charts(data, presentation, counts)
    parts = ['<section class="dashboard" aria-label="本期概况">', stats(data, counts)]
    if keys:
        parts.append('<div class="charts n' + str(len(keys)) + '">')
        for key in keys:
            parts.append(stack_chart(counts[key]) if key == 'publisher' else bar_chart(key, counts[key]))
        parts.append('</div><details class="chart-data"><summary>图表数据表</summary><div class="table-wrap">'
                     + ''.join(chart_table(key, counts[key]) for key in keys) + '</div></details>')
    parts.append('</section>')
    return ''.join(parts)


# ---------------------------------------------------------------------------
# 内容区块

def glance(data, anchors, heading):
    summary = data.get('summary') or {}
    parts = ['<section class="glance" id="glance"><h2>' + heading + '</h2><p>'
             + esc(summary.get('text') or '内容摘要尚未编制，请查看已核实事项。') + '</p>']
    if summary.get('evidence_ids'):
        parts.append(related(summary['evidence_ids'], anchors))
    parts.append('</section>')
    return ''.join(parts)


def key_figures(data, anchors):
    figures = data.get('key_figures') or []
    if not figures:
        return ''
    parts = ['<section class="figures" aria-label="关键数字"><h2 class="block-title">关键数字</h2><div class="figure-grid">']
    for figure in figures:
        parts.append('<div class="figure"><span class="figure-label">' + esc(figure['label']) + '</span><strong class="figure-value">'
                     + esc(figure['value']) + '</strong><p class="figure-note">' + esc(figure['note']) + '</p>'
                     + related(figure['evidence_ids'], anchors, '依据') + '</div>')
    parts.append('</div></section>')
    return ''.join(parts)


def pool_panel(data, anchors):
    """关注池动态：本期命中的条目及其事项链接；高优先级条目无动态时单列，不推断原因。"""
    pool = data.get('pool') or []
    if not pool:
        return ''
    hits = {}
    for item in data['items']:
        for ref in item.get('pool_ids', []):
            hits.setdefault(ref.strip(), []).append(item['id'].strip())
    order = {'high': 0, 'normal': 1, 'low': 2}
    hit_entries = sorted((entry for entry in pool if entry['id'].strip() in hits), key=lambda e: order[e.get('priority', 'normal')])
    quiet = [entry for entry in pool if entry.get('priority') == 'high' and entry['id'].strip() not in hits]
    parts = ['<section class="pool-panel" aria-label="关注池动态"><h2 class="block-title">关注池动态</h2>']
    if hit_entries:
        parts.append('<ul class="pool-list">')
        for entry in hit_entries:
            ids = hits[entry['id'].strip()]
            parts.append('<li><span class="pool-type">' + POOL_TYPE_LABELS[entry['type']] + '</span><b>' + esc(entry['name']) + '</b>'
                         + ('<span class="badge priority">重点</span>' if entry.get('priority') == 'high' else '')
                         + '<span class="pool-count">' + str(len(ids)) + '条</span>' + related(ids, anchors, '') + '</li>')
        parts.append('</ul>')
    else:
        parts.append('<p class="pool-empty">本期已核实事项未命中关注池条目。</p>')
    if quiet:
        parts.append('<p class="pool-quiet"><b>高优先级条目本期未见已核实公开动态：</b>' + '、'.join(esc(e['name']) for e in quiet)
                     + '。未检索到不等于没有事项发生。</p>')
    parts.append('<p class="pool-meta">关注池共' + str(len(pool)) + '项，本期命中' + str(len(hit_entries)) + '项。</p></section>')
    return ''.join(parts)


def watchlist(data, anchors, heading):
    entries = data.get('watchlist') or []
    if not entries:
        return ''
    parts = ['<section class="watchlist">' + heading + '<ol class="watch-list">']
    for entry in entries:
        parts.append('<li><p>' + esc(entry['text']) + '</p>' + related(entry.get('evidence_ids', []), anchors) + '</li>')
    parts.append('</ol></section>')
    return ''.join(parts)


def grouped(items):
    groups = {}
    for item in items:
        groups.setdefault(category(item), {}).setdefault(subgroup_of(item), []).append(item)
    return groups


POOL_TYPE_LABELS = {'enterprise': '企业', 'project': '项目', 'park': '园区', 'topic': '专题', 'policy': '政策'}


def pool_index(data):
    return {entry['id'].strip(): entry for entry in data.get('pool', [])}


def badges(item, pool=None):
    parts = []
    for ref in item.get('pool_ids', []):
        entry = (pool or {}).get(ref.strip())
        if entry:
            parts.append('<span class="badge pool">关注池 · ' + esc(entry['name']) + '</span>')
    if item.get('priority') == 'high':
        parts.append('<span class="badge priority">重点</span>')
    if 'publisher_type' in item:
        name = PUBLISHER_LABELS[item['publisher_type']]
        parts.append('<span class="badge"><i class="dot s' + str(PUBLISHER_SLOTS[name]) + '"></i>' + name + '</span>')
    return ''.join(parts)


def verified_label(item):
    return '一手来源已核实' if item['verification'] == 'primary_verified' else '独立来源已核实'


def reader_text(item):
    return (item.get('reader_summary') or item['summary']).strip()


def reader_analysis(item):
    return item.get('reader_analysis', item['analysis']).strip()


def subgroup_of(item):
    return item['section'].split('·', 1)[1].strip() if '·' in item['section'] else '已核实事项'


def card_item(item, anchor, pool=None):
    number = anchor.split('-')[-1]
    parts = ['<article class="item" id="' + anchor + '"><div class="item-head"><span class="item-no">' + number.zfill(2) + '</span>'
             + ('<span class="item-dept">' + esc(subgroup_of(item)) + '</span>' if '·' in item['section'] else '') + badges(item, pool) + '<span class="item-region">' + esc(item['region'])
             + '</span><span class="item-date">发布 '
             + esc(moment(item['published_at']).strftime('%Y-%m-%d')) + '</span></div><h4>' + esc(item['title'])
             + '</h4><h5 class="facts-label">公开事实</h5><p class="fact">' + esc(reader_text(item)) + '</p>']
    chips = [('阶段', item.get('stage', '')), ('主题', item.get('theme', ''))]
    if any(value for _, value in chips):
        parts.append('<ul class="chips">' + ''.join('<li class="chip"><b>' + label + '</b>' + esc(value) + '</li>' for label, value in chips if value) + '</ul>')
    analysis = reader_analysis(item)
    if analysis:
        parts.append('<div class="analysis"><h5>研究关注</h5><p>' + esc(analysis) + '</p></div>')
    topic = item['topic'] if item['topic'].strip() != item.get('theme', '').strip() else ''
    meta = [('企业', item['enterprise'].strip()), ('议题', topic), ('事件日期', item['event_date'] or '未披露')]
    parts.append('<dl class="metadata">' + ''.join('<div><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>' for label, value in meta if value)
                 + '</dl><div class="sources"><span class="verified">' + CHECK + verified_label(item) + '</span>')
    for source in item['sources']:
        parts.append('<p><a href="' + esc(source['url']) + '" rel="noopener noreferrer">' + esc(source['name']) + '</a><span>发布 '
                     + esc(moment(source['published_at']).strftime('%Y-%m-%d')) + '</span></p>')
    parts.append('</div></article>')
    return ''.join(parts)


def compact_item(item, anchor, pool=None):
    number = anchor.split('-')[-1]
    meta = [esc(item['region']), '发布 ' + esc(moment(item['published_at']).strftime('%m-%d'))]
    if item.get('stage'):
        meta.append('阶段 ' + esc(item['stage']))
    if item['enterprise'].strip():
        meta.append(esc(item['enterprise']))
    parts = ['<article class="item compact-item" id="' + anchor + '"><span class="item-no">' + number + '</span><div class="item-main"><h4>'
             + esc(item['title']) + '</h4><p class="item-meta">' + badges(item, pool) + ''.join('<span>' + value + '</span>' for value in meta)
             + '</p><p class="fact">' + esc(reader_text(item)) + '</p>']
    analysis = reader_analysis(item)
    if analysis:
        parts.append('<p class="compact-analysis"><b>研究关注</b>' + esc(analysis) + '</p>')
    parts.append('<p class="compact-source"><span class="verified">' + CHECK + verified_label(item) + '</span>'
                 + '；'.join('<a href="' + esc(source['url']) + '" rel="noopener noreferrer">' + esc(source['name']) + '</a>' for source in item['sources'])
                 + '</p></div></article>')
    return ''.join(parts)


def memo_item(item, anchor, pool=None):
    number = anchor.split('-')[-1]
    parts = ['<article class="item memo-item" id="' + anchor + '"><h4><span class="item-no">' + number + '.</span>' + badges(item, pool)
             + esc(item['title']) + '</h4><p class="fact">' + esc(reader_text(item)) + '</p>']
    analysis = reader_analysis(item)
    if analysis:
        parts.append('<p class="memo-analysis"><b>研究关注：</b>' + esc(analysis) + '</p>')
    meta = [('地区', esc(item['region'])), ('阶段', esc(item.get('stage', ''))), ('主题', esc(item.get('theme', ''))),
            ('企业', esc(item['enterprise'])), ('事件日期', '<span class="nobr">' + esc(item['event_date'] or '未披露') + '</span>')]
    parts.append('<p class="memo-meta">' + '　'.join('<span class="nobr">' + label + '：</span>' + value for label, value in meta if value.strip()) + '</p>')
    sources = '；'.join('<a href="' + esc(source['url']) + '" rel="noopener noreferrer">' + esc(source['name']) + '</a><span class="nobr">（'
                       + esc(moment(source['published_at']).strftime('%Y-%m-%d')) + '发布）</span>' for source in item['sources'])
    parts.append('<p class="memo-source">来源：' + sources + '　<span class="nobr">' + verified_label(item) + '</span></p></article>')
    return ''.join(parts)


def item_sections(items, anchors, layout, chapter_start, pool=None):
    parts = []
    for n, (group, subgroups) in enumerate(grouped(items).items()):
        count = sum(len(entries) for entries in subgroups.values())
        if layout == 'memo':
            parts.append('<section class="source-section"><h2 class="chapter">' + cn_number(chapter_start + n) + '、' + esc(group) + '</h2>')
        else:
            parts.append('<section class="source-section"><div class="section-heading"><h2>' + esc(group) + '</h2><span class="count">'
                         + str(count) + '条</span></div>')
        if layout == 'standard':
            # 卡片版把二级分类写在卡片抬头，按分区统一排成双栏，避免单条子类撑满整行。
            parts.append('<div class="item-grid">')
            parts.extend(card_item(item, anchors[item['id'].strip()], pool) for entries in subgroups.values() for item in entries)
            parts.append('</div></section>')
            continue
        render = compact_item if layout == 'compact' else memo_item
        for m, (subgroup, entries) in enumerate(subgroups.items()):
            title = ('（' + cn_number(m + 1) + '）' if layout == 'memo' else '') + esc(subgroup)
            parts.append('<section class="subgroup"><h3 class="subgroup-title">' + title + '</h3>')
            parts.extend(render(item, anchors[item['id'].strip()], pool) for item in entries)
            parts.append('</section>')
        parts.append('</section>')
    return ''.join(parts)


def features(data, anchors, info, layout, chapter):
    sections = data.get('sections') or []
    if not sections:
        return ''
    if layout == 'memo':
        parts = ['<section class="features"><h2 class="chapter">' + cn_number(chapter) + '、专题分析</h2>']
        for n, section in enumerate(sections):
            parts.append('<article class="feature"><h3>（' + cn_number(n + 1) + '）' + esc(section['heading']) + '</h3><p>' + esc(section['body']) + '</p>'
                         + related(section.get('evidence_ids', []), anchors, '关联证据') + '</article>')
        parts.append('</section>')
        return ''.join(parts)
    parts = ['<section class="features"><div class="section-heading"><h2>专题分析</h2><span class="count">' + esc(info['series'])
             + '</span></div><div class="feature-grid">']
    for section in sections:
        parts.append('<article class="feature"><h3>' + esc(section['heading']) + '</h3><p>' + esc(section['body']) + '</p>'
                     + related(section.get('evidence_ids', []), anchors, '关联证据') + '</article>')
    parts.append('</div></section>')
    return ''.join(parts)


def appendix(data, ordered, anchors, audience_label):
    period, coverage = data['period'], data['coverage']
    parts = ['<section class="appendix">']
    if ordered:
        parts.append('<details><summary>完整来源与核验记录</summary><div class="details-body">')
    for item in ordered:
        parts.append('<section class="evidence-record"><h3><a href="#' + anchors[item['id'].strip()] + '">' + esc(item['title']) + '</a></h3><p>事项ID：' + esc(item['id'])
                     + '<br>核验标记：' + esc(item['verification']) + '<br>事项发布：' + esc(item['published_at']) + '<br>事件日期：' + esc(item['event_date'] or '未披露') + '</p>')
        parts.append('<p>完整采集摘要：' + esc(item['summary']) + '</p><p>分析与核验说明：' + esc(item['analysis']) + '</p><ol>')
        for source in item['sources']:
            parts.append('<li><a href="' + esc(source['url']) + '" rel="noopener noreferrer">' + esc(source['name']) + '</a><p>原文：' + esc(source['url'])
                         + '<br>发布：' + esc(source['published_at']) + '<br>检索：' + esc(source['retrieved_at']) + '</p></li>')
        parts.append('</ol></section>')
    if ordered:
        parts.append('</div></details>')
    parts.append('<details id="run-notes"><summary>编制范围、覆盖缺口与运行记录</summary><div class="details-body"><p>完整标题：' + esc(data['title'])
                 + '<br>编制范围：' + esc(data['config_summary'])
                 + ('<br>岗位画像：' + esc(data['focus']['title']) if data.get('focus', {}).get('title') else '') + '<br>阅读视角：' + esc(audience_label) + '（' + esc(data['audience']) + '）<br>时区：Asia/Shanghai</p><p>周期起点：'
                 + esc(period['start']) + '<br>周期终点（不含）：' + esc(period['end_exclusive']) + '<br>信息截止：' + esc(period['cutoff']) + '<br>日期覆盖：'
                 + esc(coverage['available_days']) + '/' + esc(coverage['expected_days']) + ' 天<br>资料策略：'
                 + esc(METHOD_LABELS[coverage['method']]) + '</p>')
    if coverage['gaps']:
        parts.append('<h3>覆盖缺口</h3><ul>' + ''.join('<li>' + esc(gap) + '</li>' for gap in coverage['gaps']) + '</ul>')
    parts.append('</div></details></section>')
    return ''.join(parts)


def render_report(data, presentation=None, audience_label=''):
    presentation = presentation or {'theme': 'navy', 'layout': 'standard', 'color_scheme': 'auto'}
    layout = presentation['layout']
    css = (Path(__file__).resolve().parent.parent / 'assets' / 'report.css').read_text(encoding='utf-8')
    items = data['items']
    ordered = [item for subgroups in grouped(items).values() for entries in subgroups.values() for item in entries]
    anchors = {item['id'].strip(): 'item-' + str(i + 1) for i, item in enumerate(ordered)}
    info = period_info(data)
    counts = tallies(items)
    scheme = presentation['color_scheme']
    body_class = 'theme-' + presentation['theme'] + ' layout-' + layout + (' scheme-light' if scheme == 'light' else '')
    parts = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta name="color-scheme" content="' + ('light' if scheme == 'light' else 'light dark') + '">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>' + esc(data['title']) + '</title><style>' + css + '</style></head><body class="' + body_class + '"><main class="page">',
             masthead(data, info), scope_and_notices(data)]
    if not items:
        parts.append('<section class="empty"><h2>本期暂无已核实事项</h2><p>现有材料不足以形成内容摘要或分布图，不能据此判断没有事件。</p></section>')
    elif layout == 'memo':
        parts.append(glance(data, anchors, '内容提要'))
        parts.append(key_figures(data, anchors))
        parts.append(pool_panel(data, anchors))
        if presentation.get('charts'):
            parts.append(dashboard(data, presentation, counts))
        else:
            primary = sum(1 for item in items if item['verification'] == 'primary_verified')
            parts.append('<p class="meta-line">本期收录已核实事项' + str(len(items)) + '条，其中一手来源核实' + str(primary) + '条；日期覆盖'
                         + str(data['coverage']['available_days']) + '/' + str(data['coverage']['expected_days']) + '天。</p>')
    else:
        parts.append(dashboard(data, presentation, counts))
        parts.append(glance(data, anchors, '一句话速览'))
        parts.append(key_figures(data, anchors))
        parts.append(pool_panel(data, anchors))
    chapter = 1
    if items:
        parts.append(item_sections(items, anchors, layout, chapter, pool_index(data)))
        chapter += len(grouped(items))
    parts.append(features(data, anchors, info, layout, chapter))
    if data.get('sections'):
        chapter += 1
    heading = ('<h2 class="chapter">' + cn_number(chapter) + '、后续关注</h2>') if layout == 'memo' else '<div class="section-heading"><h2>后续关注</h2></div>'
    parts.append(watchlist(data, anchors, heading))
    parts.append(appendix(data, ordered, anchors, audience_label))
    parts.append('<footer><p>公开信息整理 · 请以原始披露为准</p><p>' + DISCLAIMER + '</p></footer></main></body></html>')
    return ''.join(parts)
