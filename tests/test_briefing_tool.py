"""离线简报辅助工具的标准库回归测试。"""
import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
import re
from html.parser import HTMLParser
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'briefing_tool.py'
SPEC = importlib.util.spec_from_file_location('briefing_tool', SCRIPT)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def config():
    return {'schema_version': 1, 'audience': 'regional',
            'regions': [{'name': '赣州市', 'parent': '江西省', 'level': 'city'}]}


def report():
    return {'schema_version': 1, 'kind': 'daily', 'title': '公开简报', 'status': 'formal',
            'audience': 'corporate', 'config_summary': '赣州市公开事项',
            'period': {'start': '2024-02-29T00:00:00+08:00', 'end_exclusive': '2024-03-01T00:00:00+08:00', 'cutoff': '2024-03-01T00:00:00+08:00'},
            'coverage': {'expected_days': 1, 'available_days': 1, 'gaps': [], 'method': 'aggregate_only'},
            'items': [{'id': 'item-1', 'title': '示例公告', 'summary': '公开事实', 'region': '苏州市', 'enterprise': '', 'topic': '项目进展',
                       'published_at': '2024-02-29T10:00:00+08:00', 'event_date': '2024-02-29',
                       'sources': [{'name': '公开来源', 'url': 'https://example.com/article?a=1&b=2',
                                    'published_at': '2024-02-29T10:00:00+08:00', 'retrieved_at': '2024-02-29T12:00:00+08:00'}],
                       'verification': 'primary_verified', 'analysis': '分析结论', 'section': '公开事项', 'event_key': 'event-1'}]}


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        value = tool.validate_config(config())
        self.assertEqual(value['max_items'], 12)
        self.assertEqual(value['timezone'], 'Asia/Shanghai')
        self.assertEqual(value['watch_pool'], [])
        self.assertEqual(value['role_profile']['focus_directions'], [])
        self.assertEqual(value['topics'], [])

    def test_trim(self):
        value = config()
        value['config_name'] = ' 测试 '
        self.assertEqual(tool.validate_config(value)['config_name'], '测试')

    def test_missing_field_path(self):
        value = config()
        del value['audience']
        with self.assertRaisesRegex(tool.ValidationError, r'\$\.audience'):
            tool.validate_config(value)

    def test_unknown_top_level(self):
        value = config()
        value['secret'] = 'sensitive'
        with self.assertRaises(tool.ValidationError) as ctx:
            tool.validate_config(value)
        self.assertNotIn('sensitive', str(ctx.exception))

    def test_unknown_nested(self):
        value = config()
        value['regions'][0]['extra'] = 1
        with self.assertRaisesRegex(tool.ValidationError, r'\$\.regions\[0\]'):
            tool.validate_config(value)

    def test_bool_version_and_count(self):
        for field in ('schema_version', 'max_items'):
            value = config()
            value[field] = True
            with self.subTest(field=field), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)

    def test_regions_empty_or_duplicate(self):
        for regions in ([], config()['regions'] * 2):
            value = config()
            value['regions'] = regions
            with self.subTest(regions=regions), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)

    def test_limits(self):
        for field, value in [('regions', [{'name': f'县{i}', 'parent': '赣州市', 'level': 'county'} for i in range(31)]), ('topics', ['a'] * 31), ('watch_pool', [{'id': f'p{i}', 'name': str(i), 'type': 'topic', 'public_ok': True} for i in range(301)]), ('max_items', 51), ('config_name', 'a' * 81)]:
            data = config()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(tool.ValidationError):
                tool.validate_config(data)

    def test_strict_types(self):
        for field, value in [('topics', 'text'), ('regions', {}), ('audience', 1), ('max_items', 1.0)]:
            data = config()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(tool.ValidationError):
                tool.validate_config(data)

    def test_text_controls_and_empty(self):
        for name in ('  ', 'a\nb', 'a\x00b', 'a\x7fb', 'a' * 201):
            value = config()
            value['regions'][0]['name'] = name
            with self.subTest(name=name), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)

    def test_natural_text_preserved(self):
        value = config()
        value['topics'] = ['研究<script>alert(1)</script>技术']
        self.assertEqual(tool.validate_config(value)['topics'], value['topics'])

    def test_duplicate_topics(self):
        value = config()
        value['topics'] = ['项目进展', ' 项目进展 ']
        with self.assertRaisesRegex(tool.ValidationError, r'\$\.topics'):
            tool.validate_config(value)

    def test_float_version(self):
        value = config()
        value['schema_version'] = 1.0
        with self.assertRaisesRegex(tool.ValidationError, r'\$\.schema_version'):
            tool.validate_config(value)
        data = report()
        data['schema_version'] = 1.0
        with self.assertRaisesRegex(tool.ValidationError, r'\$\.schema_version'):
            tool.render_report(data)

    def test_invalid_enums(self):
        for field in ('timezone', 'audience', 'scope_mode', 'history_mode'):
            value = config()
            value[field] = 'invalid'
            with self.subTest(field=field), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)


class NewConfigurationTests(unittest.TestCase):
    def test_all_reading_views_flow_through_config_and_report(self):
        for audience in tool.AUDIENCES:
            selected = config()
            selected['audience'] = audience
            selected['role_profile'] = {'title': '示例岗位'}
            normalized = tool.validate_config(selected)
            data = report()
            data['audience'] = normalized['audience']
            with self.subTest(audience=audience):
                self.assertIn('赣州市公开事项', tool.render_report(data))

    def test_source_filters_are_explicit_and_strict(self):
        value = config()
        self.assertEqual(tool.validate_config(value)['source_types'], ['gov', 'enterprise', 'other'])
        for content in ([], ['gov', 'gov'], ['internal'], 'gov', [True]):
            value['source_types'] = content
            with self.subTest(content=content), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)
        value['source_types'] = ['enterprise']
        self.assertEqual(tool.validate_config(value)['source_types'], ['enterprise'])

    def test_only_selected_topic_requires_topics(self):
        value = config()
        value['require_topic_match'] = True
        with self.assertRaisesRegex(tool.ValidationError, 'topics'):
            tool.validate_config(value)
        value['topics'] = ['绿色循环']
        self.assertTrue(tool.validate_config(value)['require_topic_match'])
        value['require_topic_match'] = 1
        with self.assertRaises(tool.ValidationError):
            tool.validate_config(value)


class JsonTests(unittest.TestCase):
    def check_bad(self, content):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.json'
            path.write_bytes(content)
            with self.assertRaises(tool.ValidationError):
                tool.load_json(path)

    def test_duplicate_keys(self):
        self.check_bad(b'{"a": {"b": 1, "b": 2}}')

    def test_non_finite(self):
        for value in (b'NaN', b'Infinity', b'-Infinity', b'1e9999'):
            with self.subTest(value=value):
                self.check_bad(value)

    def test_size_cap(self):
        self.check_bad(b' ' * (tool.MAX_BYTES + 1))

    def test_encoding_and_syntax(self):
        for content in (b'\xff', b'{', b'{} garbage'):
            with self.subTest(content=content):
                self.check_bad(content)

    def test_valid_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.json'
            path.write_text('{"value": 1.5}', encoding='utf-8')
            self.assertEqual(tool.load_json(path), {'value': 1.5})


class WindowTests(unittest.TestCase):
    def test_daily_previous_day(self):
        value = tool.compute_window('daily', '2024-03-01T09:00:00+08:00')
        self.assertEqual(value['start'], '2024-02-29T00:00:00+08:00')
        self.assertEqual(value['cutoff'], '2024-03-01T09:00:00+08:00')

    def test_weekly_iso_cross_year(self):
        value = tool.compute_window('weekly', '2021-01-04T09:00:00+08:00')
        self.assertEqual(value['start'], '2020-12-28T00:00:00+08:00')
        self.assertEqual(value['end_exclusive'], '2021-01-04T00:00:00+08:00')
        explicit = tool.compute_window('weekly', '2021-01-04T09:00:00+08:00', '2020-W53')
        self.assertEqual(value, explicit)

    def test_monthly_leap_and_year(self):
        self.assertEqual(tool.compute_window('monthly', '2024-03-03T00:00:00Z')['start'], '2024-02-01T00:00:00+08:00')
        self.assertEqual(tool.compute_window('monthly', '2024-01-02T00:00:00Z')['start'], '2023-12-01T00:00:00+08:00')

    def test_current_period_cutoff(self):
        for kind, period in [('daily', '2024-03-15'), ('weekly', '2024-W11'), ('monthly', '2024-03')]:
            value = tool.compute_window(kind, '2024-03-15T09:00:00+08:00', period)
            self.assertEqual(value['cutoff'], '2024-03-15T09:00:00+08:00')

    def test_current_period_end_clipped_and_zero_rejected(self):
        value = tool.compute_window('daily', '2024-03-15T09:00:00+08:00', '2024-03-15')
        self.assertEqual(value['end_exclusive'], value['cutoff'])
        with self.assertRaises(tool.ValidationError):
            tool.compute_window('daily', '2024-03-15T00:00:00+08:00', '2024-03-15')

    def test_future_period(self):
        for kind, period in [('daily', '2024-03-16'), ('weekly', '2024-W12'), ('monthly', '2024-04')]:
            with self.subTest(kind=kind), self.assertRaises(tool.ValidationError):
                tool.compute_window(kind, '2024-03-15T09:00:00+08:00', period)

    def test_strict_periods(self):
        for kind, period in [('daily', '2024-2-01'), ('daily', '2023-02-29'), ('weekly', '2024-W1'), ('weekly', '2021-W53'), ('monthly', '2024-2'), ('monthly', '2024-13')]:
            with self.subTest(period=period), self.assertRaises(tool.ValidationError):
                tool.compute_window(kind, '2025-01-01T00:00:00Z', period)

    def test_no_timezone(self):
        with self.assertRaises(tool.ValidationError):
            tool.compute_window('daily', '2024-03-15T09:00:00')

    def test_invalid_timezone_offsets(self):
        for offset in ('+08:60', '+08:99', '-10:99', '+24:00'):
            with self.subTest(offset=offset), self.assertRaises(tool.ValidationError):
                tool.compute_window('daily', '2024-03-15T09:00:00' + offset)

    def test_timezone_converted(self):
        self.assertEqual(tool.compute_window('daily', '2024-03-01T17:00:00Z')['start'], '2024-03-01T00:00:00+08:00')


class RenderTests(unittest.TestCase):
    def assert_bad(self, value, path=None):
        with self.assertRaises(tool.ValidationError) as ctx:
            tool.render_report(value)
        if path:
            self.assertIn(path, str(ctx.exception))

    def test_deterministic_complete_html(self):
        data = report()
        output = tool.render_report(data)
        self.assertEqual(output, tool.render_report(copy.deepcopy(data)))
        self.assertIn('公开事实', output)
        self.assertIn('研究关注', output)
        self.assertIn('检索：2024-02-29T12:00:00+08:00', output)
        self.assertIn('href="https://example.com/article?a=1&amp;b=2"', output)
        self.assertNotIn('<script', output)

    def test_morning_retrieval_after_publication_window(self):
        data = report()
        data['period']['cutoff'] = '2024-03-01T07:00:00+08:00'
        data['items'][0]['sources'][0]['retrieved_at'] = '2024-03-01T06:00:00+08:00'
        output = tool.render_report(data)
        self.assertIn('检索：2024-03-01T06:00:00+08:00', output)
        self.assertIn('本报告基于公开信息整理', output)
        self.assertIn('赣州市公开事项', output)
        self.assertNotIn('全省公开信息', output)
        self.assertIn('仅汇总已有记录', output)

    def test_independent_requires_two_distinct_urls(self):
        data = report()
        item = data['items'][0]
        item['verification'] = 'independent_verified'
        self.assert_bad(data, '.sources')
        item['sources'].append(copy.deepcopy(item['sources'][0]))
        self.assert_bad(data, '.sources')
        item['sources'][1]['url'] = 'https://example.org/article'
        self.assertIn('独立来源已核实', tool.render_report(data))

    def test_xss_escaped(self):
        data = report()
        payload = '<script>alert("x")</script>'
        data['title'] = payload
        data['items'][0]['summary'] = payload
        data['items'][0]['sources'][0]['name'] = payload
        output = tool.render_report(data)
        self.assertNotIn('<script>', output)
        self.assertGreaterEqual(output.count('&lt;script&gt;'), 4)

    def test_unsafe_urls(self):
        urls = ['file:///etc/passwd', 'javascript:alert(1)', 'data:text/html,hi', 'https://u:p@example.com', 'http://localhost/x', 'http://a.localhost./', 'http://127.0.0.1', 'http://10.1.2.3', 'http://172.16.0.1', 'http://192.168.1.1', 'http://169.254.169.254', 'http://0.0.0.0', 'http://[::1]', 'http://[::ffff:127.0.0.1]', 'http://2130706433', 'http://127.1', 'http://0x7f000001', 'http://0177.0.0.1', 'https://a.internal', 'https://a.local', 'https://example.com\\@localhost', 'https://example.com:99999', 'https://exa mple.com']
        for url in urls:
            data = report()
            data['items'][0]['sources'][0]['url'] = url
            with self.subTest(url=url):
                self.assert_bad(data, '.url')

    def test_credential_query_and_fragment_urls(self):
        for component in ('?token=SECRET', '?ACCESS_TOKEN=SECRET', '?api_key=SECRET', '?key=SECRET', '?authorization=SECRET', '?signature=SECRET', '?X-Amz-Signature=SECRET', '?X-Amz-Credential=SECRET', '?X-Goog-Signature=SECRET', '#access_token=SECRET', '#/route?access_token=SECRET', '?%74oken=SECRET'):
            with self.subTest(component=component), self.assertRaises(tool.ValidationError) as ctx:
                tool.safe_url('https://example.com/a' + component, '$.url')
            self.assertNotIn('SECRET', str(ctx.exception))

    def test_expected_days_matches_natural_days(self):
        data = report()
        data['kind'] = 'monthly'
        data['period']['start'] = '2024-02-01T00:00:00+08:00'
        self.assert_bad(data, '$.coverage.expected_days')
        data['coverage'].update(expected_days=29, available_days=29)
        self.assertIn('29/29 天', tool.render_report(data))
        data['period']['end_exclusive'] = '2024-03-01T01:00:00+08:00'
        data['period']['cutoff'] = '2024-03-01T07:00:00+08:00'
        self.assert_bad(data, '$.period.end_exclusive')

    def test_item_date_requires_matching_source(self):
        data = report()
        data['items'][0]['sources'][0]['published_at'] = '2024-02-28T10:00:00+08:00'
        self.assert_bad(data, '$.items[0].published_at')
        source = copy.deepcopy(data['items'][0]['sources'][0])
        source.update(url='https://example.org/a', published_at='2024-02-29T02:00:00Z')
        data['items'][0]['sources'].append(source)
        self.assertIn('公开事实', tool.render_report(data))

    def test_public_urls(self):
        for url in ['https://example.com', 'http://8.8.8.8/info', 'https://[2606:4700:4700::1111]/']:
            self.assertEqual(tool.safe_url(url, '$.url'), url)

    def test_missing_source(self):
        data = report()
        data['items'][0]['sources'] = []
        self.assert_bad(data, '.sources')

    def test_missing_field(self):
        data = report()
        del data['items'][0]['summary']
        self.assert_bad(data, '$.items[0].summary')

    def test_unknown_fields_everywhere(self):
        for getter in (lambda d: d, lambda d: d['period'], lambda d: d['coverage'], lambda d: d['items'][0], lambda d: d['items'][0]['sources'][0]):
            data = report()
            getter(data)['extra'] = True
            self.assert_bad(data)

    def test_empty_formal_and_observation(self):
        data = report()
        data['items'] = []
        self.assert_bad(data, '$.items')
        data['status'] = 'observation'
        self.assertIn('暂无已核实事项', tool.render_report(data))

    def test_formal_coverage_gap(self):
        data = report()
        data['coverage']['gaps'] = ['2024-02-29']
        self.assert_bad(data, '$.coverage')
        data['status'] = 'observation'
        self.assertIn('覆盖缺口', tool.render_report(data))

    def test_formal_available_short(self):
        data = report()
        data['coverage']['available_days'] = 0
        self.assert_bad(data, '$.coverage')

    def test_bool_counts(self):
        for field in ('expected_days', 'available_days'):
            data = report()
            data['coverage'][field] = True
            self.assert_bad(data, '$.coverage.' + field)

    def test_unverified_rejected_in_all_statuses(self):
        for status in ('formal', 'observation'):
            data = report()
            data['status'] = status
            data['items'][0]['verification'] = 'unverified'
            self.assert_bad(data, '.verification')

    def test_duplicate_ids_events(self):
        for field in ('id', 'event_key'):
            data = report()
            second = copy.deepcopy(data['items'][0])
            second['id'] = 'item-2'
            second['event_key'] = 'event-2'
            second[field] = data['items'][0][field]
            data['items'].append(second)
            self.assert_bad(data, '.' + field)

    def test_item_outside_period_or_future(self):
        for published in ('2024-02-28T23:59:59+08:00', '2024-03-01T00:00:00+08:00', '2024-02-29T23:00:00+08:00'):
            data = report()
            data['period']['end_exclusive'] = '2024-02-29T22:00:00+08:00'
            data['period']['cutoff'] = '2024-02-29T22:00:00+08:00'
            data['status'] = 'observation'
            data['coverage']['gaps'] = ['部分周期']
            data['items'][0]['published_at'] = published
            self.assert_bad(data, '.published_at')

    def test_source_future_and_retrieval_order(self):
        for published, retrieved in [('2024-03-02T00:00:00Z', '2024-03-02T01:00:00Z'), ('2024-02-29T10:00:00+08:00', '2024-03-02T00:00:00Z'), ('2024-02-29T10:00:00+08:00', '2024-02-29T09:00:00+08:00')]:
            data = report()
            data['items'][0]['sources'][0].update(published_at=published, retrieved_at=retrieved)
            self.assert_bad(data, '.sources[0]')

    def test_naive_timestamp(self):
        data = report()
        data['items'][0]['sources'][0]['retrieved_at'] = '2024-02-29T12:00:00'
        self.assert_bad(data, '.retrieved_at')

    def test_invalid_period_order(self):
        data = report()
        data['period']['cutoff'] = '2024-02-29T00:00:00+08:00'
        self.assert_bad(data, '$.period')

    def test_event_date_future(self):
        data = report()
        data['items'][0]['event_date'] = '2024-03-02'
        self.assert_bad(data, '.event_date')

    def test_monthly_sections(self):
        data = report()
        data['kind'] = 'monthly'
        data['period']['start'] = '2024-02-01T00:00:00+08:00'
        data['coverage'].update(expected_days=29, available_days=29)
        data['sections'] = [{'heading': '专题', 'body': '判断', 'evidence_ids': ['item-1']}]
        self.assertIn('href="#item-1"', tool.render_report(data))
        data['sections'][0]['evidence_ids'] = []
        self.assert_bad(data, '.evidence_ids')
        data['sections'][0]['evidence_ids'] = ['absent']
        self.assert_bad(data, '.evidence_ids')

    def test_daily_weekly_sections(self):
        for kind in ('daily', 'weekly'):
            data = report()
            data['kind'] = kind
            if kind == 'weekly':
                data['period'].update(start='2024-02-26T00:00:00+08:00', end_exclusive='2024-03-04T00:00:00+08:00', cutoff='2024-03-04T07:00:00+08:00')
                data['coverage'].update(expected_days=7, available_days=7)
            data['sections'] = [{'heading': '周期信号', 'body': '判断', 'evidence_ids': ['item-1']}]
            with self.subTest(kind=kind):
                self.assertIn('href="#item-1"', tool.render_report(data))


class NewPresentationTests(unittest.TestCase):
    def test_summary_is_provided_content_with_traceable_ids(self):
        data = report()
        data['summary'] = {'text': '企业披露项目处于规划公示阶段，尚未披露开工进展。', 'evidence_ids': ['item-1']}
        rendered = tool.render_report(data)
        self.assertIn(data['summary']['text'], rendered)
        self.assertLess(rendered.index('class="dashboard"'), rendered.index('id="glance"'))
        self.assertLess(rendered.index('id="glance"'), rendered.index('class="source-section"'))
        self.assertNotIn('本期核实1条', rendered)
        self.assertNotIn('data:image/png', rendered)
        self.assertNotIn('导出分享', rendered)

    def test_missing_summary_is_not_invented(self):
        data = report()
        data['sections'] = [{'heading': '方法', 'body': '这不是本期内容摘要。', 'evidence_ids': ['item-1']}]
        rendered = tool.render_report(data)
        self.assertIn('内容摘要尚未编制', rendered)
        self.assertNotIn('本期核实', rendered)

    def test_summary_references_must_be_present_unique_and_nonempty(self):
        for ids in ([], ['unknown'], ['item-1', 'item-1']):
            data = report()
            data['summary'] = {'text': '项目安排', 'evidence_ids': ids}
            with self.subTest(ids=ids), self.assertRaisesRegex(tool.ValidationError, 'summary.evidence_ids'):
                tool.validate_report(data)

    def test_no_summary_without_facts(self):
        data = report()
        data.update(status='observation', items=[])
        data['summary'] = {'text': '不能在无数据时写事实。', 'evidence_ids': []}
        with self.assertRaisesRegex(tool.ValidationError, 'summary.evidence_ids'):
            tool.validate_report(data)

    def test_classification_is_explicit_and_safe(self):
        data = report()
        item = data['items'][0]
        item.update(theme='绿色循环', stage='环评拟批准', publisher_type='enterprise')
        rendered = tool.render_report(data)
        self.assertIn('企业资讯', rendered)
        self.assertNotIn('非承载网站', rendered)
        self.assertNotIn('精确分配', rendered)
        self.assertIn('绿色循环', rendered)
        item['publisher_type'] = 'private'
        with self.assertRaisesRegex(tool.ValidationError, 'publisher_type'):
            tool.validate_report(data)

    def test_new_content_fields_are_escaped(self):
        data = report()
        payload = '<img src=x onerror=alert(1)>'
        data['summary'] = {'text': payload, 'evidence_ids': ['item-1']}
        data['items'][0].update(theme=payload, stage=payload, publisher_type='gov')
        rendered = tool.render_report(data)
        self.assertNotIn('<img ', rendered)
        self.assertIn('&lt;img ', rendered)
        self.assertNotIn('<script', rendered)

    def test_partial_period_requires_gap_and_visible_label(self):
        data = report()
        data.update(kind='monthly', status='observation')
        data['period'].update(start='2024-02-01T00:00:00+08:00', end_exclusive='2024-02-29T14:00:00+08:00', cutoff='2024-02-29T14:00:00+08:00')
        data['coverage'].update(expected_days=29, available_days=29)
        with self.assertRaisesRegex(tool.ValidationError, 'coverage.gaps'):
            tool.validate_report(data)
        data['coverage']['gaps'] = ['截至当日14时，非完整月。']
        rendered = tool.render_report(data)
        self.assertIn('部分月报', rendered)
        self.assertIn('截至当日14时，非完整月。', rendered[:rendered.index('<section class="dashboard"')])


class PublicationTests(unittest.TestCase):
    def test_theme_bars_include_unclassified_items_last(self):
        data = report()
        data['items'][0].update(theme='绿色循环', publisher_type='gov')
        for n in (2, 3):
            extra = copy.deepcopy(data['items'][0])
            extra.update(id=f'item-{n}', event_key=f'event-{n}')
            data['items'].append(extra)
        data['items'][2].pop('theme')
        rendered = tool.render_report(data)
        self.assertIn('含未分类1条', rendered)
        block = rendered.split('<figure class="chart chart-bars"', 1)[1].split('</figure>', 1)[0]
        rows = re.findall(r'<li( class="muted")?[^>]*><span class="bar-label">([^<]+)</span>.*?width:([\d.]+)%', block)
        self.assertEqual([(muted, name) for muted, name, _ in rows], [('', '绿色循环'), (' class="muted"', '未分类')])
        self.assertEqual([float(width) for _, _, width in rows], [100.0, 50.0])

    def test_all_missing_themes_are_not_hidden(self):
        rendered = tool.render_report(report())
        self.assertIn('含未分类1条', rendered)
        self.assertNotIn('chart-bars', rendered)

    def test_reader_text_and_collection_details_are_both_preserved(self):
        data = report()
        data['items'][0]['summary'] = '原始采集内容，日期00:00为编码占位。'
        data['items'][0]['reader_summary'] = '项目处于环评拟批准公示。'
        rendered = tool.render_report(data)
        main_text = rendered.split('<section class="appendix">', 1)[0]
        self.assertIn('项目处于环评拟批准公示。', main_text)
        self.assertNotIn('编码占位', main_text)
        self.assertIn(data['items'][0]['summary'], rendered)

    def test_reader_analysis_keeps_evidence_without_visible_implementation_notes(self):
        data = report()
        data['items'][0]['analysis'] = '正式批复尚未公开。原文与栏目仅计一个证据根。'
        data['items'][0]['reader_analysis'] = '后续关注正式批复。'
        rendered = tool.render_report(data)
        main_text = rendered.split('<section class="appendix">', 1)[0]
        self.assertIn('后续关注正式批复。', main_text)
        self.assertNotIn('证据根', main_text)
        self.assertIn(data['items'][0]['analysis'], rendered)
        data['items'][0]['reader_analysis'] = ''
        main_text = tool.render_report(data).split('<section class="appendix">', 1)[0]
        self.assertNotIn('class="analysis"', main_text)

    def test_report_omits_implementation_notes(self):
        rendered = tool.render_report(report())
        for phrase in ('非承载网站', '矩形面积按主题事项数', '已标注', '本期样本，非全省总量'):
            self.assertNotIn(phrase, rendered)
        self.assertNotIn('class="chart-note"', rendered)


class ExportBoundaryTests(unittest.TestCase):
    def exporter(self):
        import sys
        script = SCRIPT.with_name('export_png.py')
        spec = importlib.util.spec_from_file_location('png_export_boundary', script)
        exporter = importlib.util.module_from_spec(spec)
        sys.modules['briefing_tool'] = tool
        spec.loader.exec_module(exporter)
        return exporter

    def test_existing_output_is_preserved(self):
        exporter = self.exporter()
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'share.png'
            target.write_bytes(b'existing output')
            with self.assertRaisesRegex(tool.ValidationError, '禁止覆盖'):
                exporter.export_png('not-needed.json', target)
            self.assertEqual(target.read_bytes(), b'existing output')

    def test_empty_or_unsummarized_data_cannot_export(self):
        exporter = self.exporter()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.json'
            target = Path(root) / 'share.png'
            data = report()
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(tool.ValidationError, 'summary'):
                exporter.export_png(path, target)
            data.update(status='observation', items=[])
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(tool.ValidationError, 'items'):
                exporter.export_png(path, target)
            self.assertFalse(target.exists())

    def test_export_parameters_are_bounded(self):
        exporter = self.exporter()
        for kwargs in ({'width': 359}, {'scale': 4}, {'scale': True}, {'browser_channel': 'remote'}, {'theme': 'neon'}, {'layout': 'poster'}, {'executable_path': '/nonexistent/browser'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(tool.ValidationError):
                exporter.export_png('not-needed.json', 'share.png', **kwargs)


class PeriodShapeTests(unittest.TestCase):
    def test_daily_cannot_span_month(self):
        data = report()
        data['period']['start'] = '2024-02-01T00:00:00+08:00'
        with self.assertRaisesRegex(tool.ValidationError, 'end_exclusive'):
            tool.validate_report(data)

    def test_weekly_must_start_monday(self):
        data = report()
        data['kind'] = 'weekly'
        with self.assertRaisesRegex(tool.ValidationError, '周一'):
            tool.validate_report(data)

    def test_monthly_must_start_first(self):
        data = report()
        data['kind'] = 'monthly'
        with self.assertRaisesRegex(tool.ValidationError, '1日'):
            tool.validate_report(data)

    def test_partial_cannot_claim_formal(self):
        data = report()
        data['period'].update(end_exclusive='2024-02-29T14:00:00+08:00', cutoff='2024-02-29T14:00:00+08:00')
        with self.assertRaisesRegex(tool.ValidationError, '部分周期'):
            tool.validate_report(data)
        data['status'] = 'observation'
        data['coverage']['gaps'] = ['部分周期，截至14时']
        self.assertIn('观察稿', tool.render_report(data))

    def test_start_must_be_midnight(self):
        data = report()
        data['period']['start'] = '2024-02-29T01:00:00+08:00'
        with self.assertRaisesRegex(tool.ValidationError, '00:00'):
            tool.validate_report(data)


class CliTests(unittest.TestCase):
    def test_exit_two_and_redacted_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.json'
            data = config()
            data['audience'] = 'private-sensitive-text'
            path.write_text(json.dumps(data), encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(tool.main(['validate-config', str(path)]), 2)
            self.assertIn('$.audience', output.getvalue())
            self.assertNotIn('private-sensitive-text', output.getvalue())

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / 'data.json', Path(root) / 'report.html'
            source.write_text(json.dumps(report()), encoding='utf-8')
            args = ['render', '--data', str(source), '--out', str(target)]
            self.assertEqual(tool.main(args), 0)
            first = target.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tool.main(args), 2)
            self.assertEqual(target.read_bytes(), first)

    def test_normalized_config_stdout(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'config.json'
            source.write_text(json.dumps(config()), encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(tool.main(['validate-config', str(source)]), 0)
            self.assertEqual(json.loads(output.getvalue())['history_mode'], 'aggregate_only')



ROOT = SCRIPT.parents[1]


def multi_report(count=4):
    data = report()
    themes = ['先进制造', '先进制造', '绿色低碳', '数字经济', '新能源', '生物医药', '新材料', '文旅消费', '现代农业', '交通物流']
    types = ['gov', 'enterprise', 'gov', 'other']
    base = data['items'][0]
    data['items'] = []
    for n in range(count):
        item = copy.deepcopy(base)
        item.update(id=f'item-{n + 1}', event_key=f'event-{n + 1}', title=f'示例公告{n + 1}', theme=themes[n % len(themes)],
                    stage='公示', publisher_type=types[n % 4], region=['赣州市', '九江市'][n % 2],
                    section=('政府资讯 · 发展改革' if types[n % 4] == 'gov' else '企业资讯 · 公告'))
        data['items'].append(item)
    return data


class ChartTests(unittest.TestCase):
    def test_publisher_stack_uses_counts_and_shares_sum_to_100(self):
        data = multi_report(4)
        rendered = tool.render_report(data)
        block = rendered.split('<figure class="chart chart-stack"', 1)[1].split('</figure>', 1)[0]
        self.assertEqual(re.findall(r'flex-grow:(\d+)', block), ['2', '1', '1'])
        shares = [int(value) for value in re.findall(r'<em>(\d+)%</em>', block)]
        self.assertEqual(sum(shares), 100)
        self.assertIn('class="seg s1"', block)

    def test_long_tail_folds_but_table_keeps_every_category(self):
        data = multi_report(10)
        rendered = tool.render_report(data, layout='standard')
        block = rendered.split('<figure class="chart chart-bars"', 1)[1].split('</figure>', 1)[0]
        self.assertEqual(block.count('<li'), 7)
        self.assertIn('其余', block)
        table = rendered.split('class="chart-data"', 1)[1]
        for theme in ('交通物流', '现代农业', '文旅消费'):
            self.assertIn(theme, table)

    def test_daily_reduces_charts_and_small_samples_skip_bars(self):
        data = multi_report(2)
        rendered = tool.render_report(data)
        self.assertIn('chart-stack', rendered)
        self.assertNotIn('chart-bars', rendered)
        data = multi_report(4)
        self.assertNotIn('事项所在地区', tool.render_report(data))

    def test_explicit_chart_selection(self):
        data = multi_report(4)
        data['presentation'] = {'charts': ['region', 'stage']}
        rendered = tool.render_report(data)
        self.assertIn('事项所在地区', rendered)
        self.assertNotIn('chart-stack', rendered)
        self.assertNotIn('所处阶段', rendered)  # 单一阶段不足以成图
        data['presentation'] = {'charts': []}
        self.assertNotIn('class="charts', tool.render_report(data))
        for charts in (['region', 'region'], ['funnel']):
            data['presentation'] = {'charts': charts}
            with self.subTest(charts=charts), self.assertRaises(tool.ValidationError):
                tool.validate_report(data)


class TemplateTests(unittest.TestCase):
    def test_every_theme_and_layout_is_self_contained_and_deterministic(self):
        data = multi_report(5)
        data['summary'] = {'text': '示例主体发布项目公示。', 'evidence_ids': ['item-1']}
        for theme in tool.THEMES:
            for layout in tool.LAYOUTS:
                with self.subTest(theme=theme, layout=layout):
                    first = tool.render_report(copy.deepcopy(data), theme, layout)
                    self.assertEqual(first, tool.render_report(copy.deepcopy(data), theme, layout))
                    self.assertIn(f'class="theme-{theme} layout-{layout}"', first)
                    for banned in ('<script', 'http://fonts', '<img', 'data:image'):
                        self.assertNotIn(banned, first)
                    self.assertIn('id="glance"', first)
                    self.assertIn('href="#item-1"', first)

    def test_cli_options_override_report_presentation(self):
        data = report()
        data['presentation'] = {'theme': 'navy', 'layout': 'compact', 'color_scheme': 'light'}
        rendered = tool.render_report(data)
        self.assertIn('class="theme-navy layout-compact scheme-light"', rendered)
        self.assertIn('content="light"', rendered)
        self.assertIn('class="theme-jade layout-memo scheme-light"', tool.render_report(data, 'jade', 'memo'))
        with self.assertRaises(tool.ValidationError):
            tool.render_report(data, theme='neon')
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / 'data.json', Path(root) / 'out.html'
            source.write_text(json.dumps(report()), encoding='utf-8')
            self.assertEqual(tool.main(['render', '--data', str(source), '--out', str(target), '--theme', 'ink', '--layout', 'memo']), 0)
            self.assertIn('theme-ink layout-memo', target.read_text(encoding='utf-8'))

    def test_memo_puts_summary_first_and_numbers_chapters(self):
        data = multi_report(4)
        data['summary'] = {'text': '示例主体发布项目公示。', 'evidence_ids': ['item-1']}
        data['sections'] = [{'heading': '周期信号', 'body': '判断', 'evidence_ids': ['item-1']}]
        data['watchlist'] = [{'text': '关注正式批复。'}]
        rendered = tool.render_report(data, layout='memo')
        self.assertLess(rendered.index('id="glance"'), rendered.index('class="source-section"'))
        self.assertNotIn('class="dashboard"', rendered)
        for heading in ('一、政府资讯', '二、企业资讯', '三、专题分析', '四、后续关注', '内容提要'):
            self.assertIn(heading, rendered)

    def test_items_are_numbered_in_display_order(self):
        data = multi_report(4)
        rendered = tool.render_report(data)
        order = re.findall(r'<article class="item" id="item-(\d+)">', rendered)
        self.assertEqual(order, ['1', '2', '3', '4'])
        titles = re.findall(r'<h4>([^<]+)</h4>', rendered)
        self.assertEqual(titles, ['示例公告1', '示例公告3', '示例公告2', '示例公告4'])

    def test_dark_mode_is_scoped_to_screen_and_light_scheme_opt_out(self):
        css = (ROOT / 'assets' / 'report.css').read_text(encoding='utf-8')
        self.assertIn('@media screen and (prefers-color-scheme:dark)', css)
        for theme in tool.THEMES:
            self.assertIn(f'.theme-{theme}{{', css)
            self.assertIn(f'.theme-{theme}:not(.scheme-light){{', css)


class ContentBlockTests(unittest.TestCase):
    def test_key_figures_cite_existing_evidence(self):
        data = report()
        data['key_figures'] = [{'label': '计划投资', 'value': '18亿元', 'note': '公告计划口径', 'evidence_ids': ['item-1']}]
        rendered = tool.render_report(data)
        self.assertIn('18亿元', rendered)
        self.assertIn('公告计划口径', rendered)
        for figure in ({'label': 'a', 'value': '1', 'note': 'n', 'evidence_ids': []},
                       {'label': 'a', 'value': '1', 'note': 'n', 'evidence_ids': ['absent']},
                       {'label': 'a', 'value': '1', 'evidence_ids': ['item-1']},
                       {'label': 'a' * 31, 'value': '1', 'note': 'n', 'evidence_ids': ['item-1']}):
            data['key_figures'] = [figure]
            with self.subTest(figure=figure), self.assertRaises(tool.ValidationError):
                tool.validate_report(data)
        data['key_figures'] = [{'label': 'a', 'value': '1', 'note': 'n', 'evidence_ids': ['item-1']}] * 7
        with self.assertRaises(tool.ValidationError):
            tool.validate_report(data)

    def test_key_figures_need_items(self):
        data = report()
        data.update(status='observation', items=[])
        data['key_figures'] = [{'label': 'a', 'value': '1', 'note': 'n', 'evidence_ids': ['item-1']}]
        with self.assertRaisesRegex(tool.ValidationError, 'key_figures'):
            tool.validate_report(data)

    def test_watchlist_issue_priority_and_escaping(self):
        data = report()
        payload = '<script>alert(1)</script>'
        data['issue'] = '第12期'
        data['watchlist'] = [{'text': payload, 'evidence_ids': ['item-1']}, {'text': '关注下期公示'}]
        data['items'][0]['priority'] = 'high'
        rendered = tool.render_report(data)
        self.assertIn('第12期', rendered)
        self.assertIn('后续关注', rendered)
        self.assertIn('关注下期公示', rendered)
        self.assertIn('>重点<', rendered)
        self.assertNotIn('<script>', rendered)
        for bad in ({'text': 'x', 'evidence_ids': ['absent']}, {'text': ''}, {'text': 'x', 'extra': 1}):
            data['watchlist'] = [bad]
            with self.subTest(bad=bad), self.assertRaises(tool.ValidationError):
                tool.validate_report(data)
        data['watchlist'] = []
        data['items'][0]['priority'] = 'urgent'
        with self.assertRaisesRegex(tool.ValidationError, 'priority'):
            tool.validate_report(data)
        data['items'][0]['priority'] = 'normal'
        data['issue'] = 'x' * 31
        with self.assertRaisesRegex(tool.ValidationError, 'issue'):
            tool.validate_report(data)

    def test_run_notes_show_readable_audience(self):
        data = report()
        data['audience'] = 'green_finance'
        self.assertIn('阅读视角：绿色金融（green_finance）', tool.render_report(data))
        self.assertEqual(set(tool.AUDIENCES), set(tool.AUDIENCE_LABELS))


class SpecialKindTests(unittest.TestCase):
    def special(self):
        data = report()
        data.update(kind='special', title='企业专题追踪')
        data['period'].update(start='2024-02-01T00:00:00+08:00', end_exclusive='2024-03-01T00:00:00+08:00', cutoff='2024-03-01T00:00:00+08:00')
        data['coverage'].update(expected_days=29, available_days=29)
        return data

    def test_window_special_inclusive_dates(self):
        value = tool.compute_window('special', '2024-03-15T09:00:00+08:00', '2024-02-01/2024-02-29')
        self.assertEqual(value['start'], '2024-02-01T00:00:00+08:00')
        self.assertEqual(value['end_exclusive'], '2024-03-01T00:00:00+08:00')
        clipped = tool.compute_window('special', '2024-03-15T09:00:00+08:00', '2024-03-01/2024-03-31')
        self.assertEqual(clipped['end_exclusive'], '2024-03-15T09:00:00+08:00')
        for period in (None, '2024-02-01', '2024-02-29/2024-02-01', '2023-01-01/2024-01-02', '2024-03-16/2024-03-20'):
            with self.subTest(period=period), self.assertRaises(tool.ValidationError):
                tool.compute_window('special', '2024-03-15T09:00:00+08:00', period)

    def test_special_report_renders_with_custom_span(self):
        data = self.special()
        rendered = tool.render_report(data)
        self.assertIn('专题观察', rendered)
        self.assertIn('2024年2月1日—2月29日', rendered)
        self.assertIn('正式简报', rendered)

    def test_special_period_limits(self):
        data = self.special()
        data['period']['start'] = '2023-02-01T00:00:00+08:00'
        data['coverage'].update(expected_days=394, available_days=394)
        with self.assertRaises(tool.ValidationError):
            tool.validate_report(data)
        data = self.special()
        data['period']['start'] = '2024-02-01T08:00:00+08:00'
        with self.assertRaisesRegex(tool.ValidationError, '00:00'):
            tool.validate_report(data)


class RegionScopeTests(unittest.TestCase):
    def test_bad_presentation_in_config(self):
        for presentation in ({'theme': 'neon'}, {'layout': 'poster'}, {'color_scheme': 'dark'}, {'font': 'x'}, 'navy'):
            value = config()
            value['presentation'] = presentation
            with self.subTest(presentation=presentation), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)


class LintTests(unittest.TestCase):
    def levels(self, data):
        return [(f['level'], f['path']) for f in tool.lint_report(data)['findings']]

    def test_clean_report_scores_high(self):
        data = report()
        data['items'][0].update(theme='先进制造', stage='公示', publisher_type='gov')
        data['summary'] = {'text': '示例企业年产5万吨项目进入环评拟批准公示，公示期5个工作日，审批决定尚未公开；同期另一项目完成备案，均处于前期手续阶段。', 'evidence_ids': ['item-1']}
        result = tool.lint_report(data)
        self.assertEqual(result['errors'], 0, result)
        self.assertEqual(result['warnings'], 0, result)
        self.assertGreaterEqual(result['score'], 95)

    def test_count_template_summary_is_error(self):
        data = report()
        data['summary'] = {'text': '本期共收录12条，政府资讯8条，企业资讯4条。', 'evidence_ids': ['item-1']}
        self.assertIn(('error', '$.summary.text'), self.levels(data))

    def test_reader_text_rules(self):
        data = report()
        item = data['items'][0]
        item['reader_summary'] = '日期00:00为编码占位。'
        item['reader_analysis'] = '该企业贷款需求较大，可作为营销机会。'
        item['title'] = '重磅！示例项目开工'
        found = self.levels(data)
        self.assertIn(('error', '$.items[0].reader_summary'), found)
        self.assertIn(('error', '$.items[0].reader_analysis'), found)
        self.assertIn(('warning', '$.items[0].title'), found)
        self.assertIn(('warning', '$.items[0]'), found)  # 缺少theme/stage/publisher_type

    def test_process_notes_allowed_in_collection_fields_when_reader_text_exists(self):
        data = report()
        data['items'][0].update(summary='网页日期00:00为编码占位。', reader_summary='项目进入公示。', theme='a', stage='b', publisher_type='gov')
        self.assertNotIn('error', [level for level, _ in self.levels(data)])

    def test_fragmented_themes_and_missing_sections(self):
        data = multi_report(8)
        data['kind'] = 'weekly'
        data['period'].update(start='2024-02-26T00:00:00+08:00', end_exclusive='2024-03-04T00:00:00+08:00', cutoff='2024-03-04T07:00:00+08:00')
        data['coverage'].update(expected_days=7, available_days=7)
        for n, item in enumerate(data['items']):
            item['theme'] = f'主题{n}'
        found = self.levels(data)
        self.assertIn(('warning', '$.items'), found)
        self.assertIn(('warning', '$.sections'), found)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'data.json'
            data = report()
            data['summary'] = {'text': '本期共收录1条。', 'evidence_ids': ['item-1']}
            path.write_text(json.dumps(data), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(tool.main(['lint', '--data', str(path)]), 1)
            self.assertIn('"errors": 1', output.getvalue())
            del data['summary']
            path.write_text(json.dumps(data), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tool.main(['lint', '--data', str(path)]), 0)
                self.assertEqual(tool.main(['lint', '--data', str(path), '--strict']), 1)

    def test_options_lists_templates(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tool.main(['options']), 0)
        value = json.loads(output.getvalue())
        self.assertEqual(set(value['themes']), set(tool.THEMES))
        self.assertIn('special', value['kinds'])



class ReviewRegressionTests(unittest.TestCase):
    """独立审查发现的问题回归。"""

    def lint_levels(self, data):
        return [(f['level'], f['path']) for f in tool.lint_report(data)['findings']]

    def test_policy_counts_and_terms_are_not_lint_errors(self):
        data = report()
        data['items'][0].update(theme='a', stage='b', publisher_type='gov')
        for text in ('甲市政府发布12条措施支持制造业数字化转型，明确设立专项资金并自印发之日起施行；同日乙市启动首批项目申报。',
                     '国务院今日发布24条措施稳外贸，示例省同步出台配套细则，两项文件均已公开原文并明确执行期限与适用范围。'):
            data['summary'] = {'text': text, 'evidence_ids': ['item-1']}
            with self.subTest(text=text):
                self.assertNotIn(('error', '$.summary.text'), self.lint_levels(data))
        data['items'][0]['reader_summary'] = '示例银行与省政府签约，提供意向授信额度1000亿元，并推广智能抓取机器人等颠覆性技术应用。'
        self.assertEqual(tool.lint_report(data)['errors'], 0, tool.lint_report(data))
        self.assertNotIn(('warning', '$.items[0].reader_summary'), self.lint_levels(data))

    def test_self_referential_counts_still_flagged(self):
        data = report()
        for text in ('本期共收录12条，政府资讯8条，企业资讯4条。', '本周12条事项，主要集中在制造业。', '政府资讯8条、企业来源4条。'):
            data['summary'] = {'text': text, 'evidence_ids': ['item-1']}
            with self.subTest(text=text):
                self.assertIn(('error', '$.summary.text'), self.lint_levels(data))

    def test_every_published_field_is_scanned(self):
        data = report()
        data['items'][0].update(theme='a', stage='b', publisher_type='gov', reader_analysis='关注正式批复。',
                                analysis='该企业融资需求旺盛，客户经理可上门拜访。')
        found = self.lint_levels(data)
        self.assertIn(('error', '$.items[0].analysis'), found)
        data = report()
        data['items'][0]['title'] = '重磅！示例公司公告，建议买入'
        data['key_figures'] = [{'label': '目标价', 'value': '目标价35元', 'note': '研报口径', 'evidence_ids': ['item-1']}]
        found = self.lint_levels(data)
        self.assertIn(('error', '$.items[0].title'), found)
        self.assertIn(('warning', '$.items[0].title'), found)
        self.assertIn(('error', '$.key_figures[0].value'), found)
        self.assertIn(('error', '$.key_figures[0].label'), found)

    def test_credit_fact_words_warn_only_in_analysis(self):
        data = report()
        data['items'][0].update(theme='a', stage='b', publisher_type='gov', reader_analysis='后续关注授信额度的实际放款进度。')
        found = self.lint_levels(data)
        self.assertIn(('warning', '$.items[0].reader_analysis'), found)
        self.assertNotIn('error', [level for level, _ in found])

    def test_utc_publication_compares_beijing_date(self):
        data = report()
        data['items'][0].update(published_at='2024-02-28T20:00:00Z', event_date='2024-02-29', theme='a', stage='b', publisher_type='gov')
        data['items'][0]['sources'][0]['published_at'] = '2024-02-28T20:00:00Z'
        self.assertNotIn(('info', '$.items[0].event_date'), self.lint_levels(data))

    def test_special_in_progress_must_be_observation(self):
        data = report()
        data.update(kind='special')
        data['period'].update(start='2024-02-01T00:00:00+08:00', end_exclusive='2024-02-29T13:37:00+08:00', cutoff='2024-02-29T13:37:00+08:00')
        data['coverage'].update(expected_days=29, available_days=29)
        with self.assertRaisesRegex(tool.ValidationError, '部分周期'):
            tool.validate_report(data)
        data['status'] = 'observation'
        with self.assertRaisesRegex(tool.ValidationError, 'gaps'):
            tool.validate_report(data)
        data['coverage']['gaps'] = ['专题周期未结束，截至发稿。']
        rendered = tool.render_report(data)
        self.assertIn('专题观察（进行中）', rendered)
        self.assertIn('截至2月29日 13:37', rendered)
        data['period']['cutoff'] = '2024-03-05T00:00:00+08:00'
        with self.assertRaisesRegex(tool.ValidationError, '部分周期'):
            tool.validate_report(data)

    def test_window_errors_are_specific(self):
        for period, message in ((None, '专题周期必须'), ('2024-02-29/2024-02-01', '早于'), ('2023-01-01/2024-01-02', '366')):
            with self.subTest(period=period), self.assertRaisesRegex(tool.ValidationError, message):
                tool.compute_window('special', '2024-03-15T09:00:00+08:00', period)
        with self.assertRaisesRegex(tool.ValidationError, 'YYYY-Www'):
            tool.compute_window('weekly', '2024-03-15T09:00:00+08:00', '2024-10')

    def test_legacy_categories_use_neutral_colour_and_fold(self):
        data = report()
        base = data['items'][0]
        data['items'] = []
        for n, section in enumerate(['政府资讯 · 发改', '甲类', '乙类', '丙类', '丁类', '戊类']):
            item = copy.deepcopy(base)
            item.update(id=f'item-{n}', event_key=f'event-{n}', section=section)
            data['items'].append(item)
        block = tool.render_report(data).split('<figure class="chart chart-stack"', 1)[1].split('</figure>', 1)[0]
        self.assertEqual(re.findall(r'class="seg (s\d)"', block), ['s1', 's0', 's0', 's0'])
        self.assertIn('其余3类', block)

    def test_print_rules_reset_every_layout(self):
        css = (ROOT / 'assets' / 'report.css').read_text(encoding='utf-8')
        print_block = css.split('@media print{', 1)[1]
        self.assertIn('.page,.layout-compact .page,.layout-memo .page{width:100%;margin:0;padding:0', print_block)
        tablet = css.split('@media(max-width:900px){', 1)[1].split('}\n@media', 1)[0]
        self.assertIn('.layout-compact .charts{grid-template-columns:1fr}', tablet)


class SecondReviewRegressionTests(unittest.TestCase):
    """第二轮多维审查确认的问题回归。"""

    def lint_levels(self, data):
        return [(f['level'], f['path']) for f in tool.lint_report(data)['findings']]

    def clean(self):
        data = report()
        data['items'][0].update(theme='先进制造', stage='公示', publisher_type='gov')
        return data

    def test_padded_timestamps_and_urls_are_rejected_not_crashing(self):
        for setter in (lambda d: d['period'].update(cutoff=d['period']['cutoff'] + ' '),
                       lambda d: d['items'][0].update(published_at=' ' + d['items'][0]['published_at']),
                       lambda d: d['items'][0]['sources'][0].update(url='　https://example.com/a'),
                       lambda d: d['items'][0].update(event_date='2024-02-29 ')):
            data = report()
            setter(data)
            with self.assertRaises(tool.ValidationError):
                tool.render_report(data)

    def test_invisible_and_bidi_characters_are_rejected(self):
        for value in ('建议​买入', '示例省‮', '甲⁧乙', '﻿标题', '软­连字'):
            data = report()
            data['items'][0]['region'] = value
            with self.subTest(value=repr(value)), self.assertRaisesRegex(tool.ValidationError, '不可见格式字符'):
                tool.validate_report(data)

    def test_fractional_seconds_parse_identically(self):
        value = tool.compute_window('daily', '2026-10-15T07:00:00.5+08:00')
        self.assertEqual(value['cutoff'], '2026-10-15T07:00:00.500000+08:00')
        data = report()
        data['period']['cutoff'] = '2024-03-01T00:00:00.25+08:00'
        self.assertIn('公开事实', tool.render_report(data))

    def test_window_output_pastes_into_report_period(self):
        value = tool.compute_window('weekly', '2024-03-06T09:00:00+08:00')
        self.assertEqual(set(value), {'start', 'end_exclusive', 'cutoff'})

    def test_special_in_progress_cannot_end_at_midnight(self):
        with self.assertRaisesRegex(tool.ValidationError, '00:00'):
            tool.compute_window('special', '2026-10-15T00:00:00+08:00', '2026-10-01/2026-10-31')
        value = tool.compute_window('special', '2026-11-01T00:00:00+08:00', '2026-10-01/2026-10-31')
        self.assertEqual(value['end_exclusive'], '2026-11-01T00:00:00+08:00')

    def test_count_templates_in_varied_wording(self):
        data = self.clean()
        for text in ('本期共收录12条已核实事项，主要分布在先进制造、新能源和生物医药领域。', '本期收录12条公开信息，政府发布8条，企业公告4条。',
                     '本周共核实事项12条，主要集中在制造业。', '本期共12项，其中政策文件5项、项目动态4项。', '本期共精选12条区域要闻，政策类5条。'):
            data['summary'] = {'text': text, 'evidence_ids': ['item-1']}
            with self.subTest(text=text):
                self.assertIn(('error', '$.summary.text'), self.lint_levels(data))
        for text in ('本月共有3条新规施行，涉及数据要素、低空经济和营商环境，均自印发之日起执行，配套细则另行公布。',
                     '本次修订新增鼓励类条目32条，删除限制类条目5条，自2026年1月1日起施行，此前已立项项目按原目录执行。'):
            data['summary'] = {'text': text, 'evidence_ids': ['item-1']}
            with self.subTest(text=text):
                self.assertNotIn(('error', '$.summary.text'), self.lint_levels(data))

    def test_factual_policy_language_is_not_an_error(self):
        for text in ('省农业农村厅印发生猪目标价格保险试点方案，对参保养殖户给予70%保费补贴。',
                     '区委书记带队上门拜访辖区重点企业，现场协调项目用地问题。',
                     '市金融局提示警惕“稳赚不赔”非法集资宣传。',
                     '五部门印发措施，要求辖内银行满足民营企业合理贷款需求。',
                     '规上工业企业盈利好转，下游企业盈利空间收窄。'):
            data = self.clean()
            data['items'][0]['reader_summary'] = text
            with self.subTest(text=text):
                self.assertEqual([f for f in tool.lint_report(data)['findings'] if f['path'].endswith('reader_summary')], [])

    def test_advice_and_inference_phrasings_are_caught_in_analysis(self):
        for text in ('维持“增持”评级，投资者可适当加仓。', '股价有望迎来估值修复，可逢低布局。', '该企业经营稳健，具备一定授信潜力。',
                     '项目资金需求较大，可作为对公业务切入点。', '建议客户经理尽快跟进项目。', '建议上门拜访企业财务负责人。'):
            data = self.clean()
            data['items'][0]['reader_analysis'] = text
            with self.subTest(text=text):
                self.assertIn(('error', '$.items[0].reader_analysis'), self.lint_levels(data))

    def test_more_process_notes_and_fields_are_scanned(self):
        data = self.clean()
        data['items'][0]['reader_summary'] = '（检索时间：2024年2月29日）项目进入环评公示。'
        self.assertIn(('error', '$.items[0].reader_summary'), self.lint_levels(data))
        data = self.clean()
        data['items'][0]['section'] = '政府资讯·营销机会与重磅项目'
        data['items'][0]['sources'][0]['name'] = '某行客户经理可上门对接'
        found = self.lint_levels(data)
        self.assertIn(('error', '$.items[0].section'), found)
        self.assertIn(('warning', '$.items[0].section'), found)
        self.assertIn(('warning', '$.items[0].sources[0].name'), found)  # 来源原题照录，只提醒
        data = self.clean()
        data['items'][0]['analysis'] = '抓取时间为10月14日，00:00为编码占位。'
        self.assertIn(('warning', '$.items[0].analysis'), self.lint_levels(data))

    def test_folded_bucket_neither_draws_nor_sets_scale(self):
        data = multi_report(10)
        for n, item in enumerate(data['items']):
            item['theme'] = f'主题{n}'
        block = tool.render_report(data).split('<figure class="chart chart-bars"', 1)[1].split('</figure>', 1)[0]
        rest = re.search(r'<li class="muted rest"[^>]*>(.*?)</li>', block).group(1)
        self.assertIn('其余4项合计', rest)
        self.assertNotIn('bar-fill', rest)
        self.assertEqual(set(re.findall(r'width:([\d.]+)%', block)), {'100.00'})

    def test_blank_analysis_and_empty_appendix(self):
        data = report()
        data['items'][0]['analysis'] = '   '
        self.assertNotIn('class="analysis"', tool.render_report(data))
        data = report()
        data.update(status='observation', items=[])
        rendered = tool.render_report(data)
        self.assertNotIn('完整来源与核验记录', rendered)
        self.assertIn('id="run-notes"', rendered)

    def test_memo_keeps_dates_and_labels_unbroken(self):
        rendered = tool.render_report(report(), layout='memo')
        self.assertIn('<span class="nobr">（2024-02-29发布）</span>', rendered)
        self.assertIn('<span class="nobr">一手来源已核实</span>', rendered)
        self.assertIn('<span class="nobr">事件日期：</span><span class="nobr">2024-02-29</span>', rendered)

    def test_live_public_method(self):
        data = report()
        data['coverage']['method'] = 'live_public'
        self.assertIn('资料策略：当期公开采集', tool.render_report(data))
        data['coverage']['method'] = 'live'
        with self.assertRaises(tool.ValidationError):
            tool.validate_report(data)

    def test_unexpected_render_failure_is_contained(self):
        original = tool.render_report
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / 'data.json', Path(root) / 'out.html'
            source.write_text(json.dumps(report()), encoding='utf-8')
            tool.render_report = lambda *args: (_ for _ in ()).throw(RuntimeError('/home/secret/path'))
            try:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(tool.main(['render', '--data', str(source), '--out', str(target)]), 2)
            finally:
                tool.render_report = original
            self.assertNotIn('secret', err.getvalue())
            self.assertFalse(target.exists())

    def test_css_wrapping_compact_note_and_print_details(self):
        css = (ROOT / 'assets' / 'report.css').read_text(encoding='utf-8')
        self.assertIn('.page{overflow-wrap:anywhere;', css)
        self.assertIn('.layout-compact .stats dd:not(.stat-note){font-size:22px}', css)
        self.assertIn('details::details-content{content-visibility:visible', css)
        self.assertIn('.item-grid>.item{padding:18px 16px}', css)
        self.assertTrue(tool.sys.dont_write_bytecode)


class VerificationRoundTests(unittest.TestCase):
    """修复复核轮发现的回归。"""

    def findings(self, field, text):
        data = report()
        data['items'][0].update(theme='a', stage='b', publisher_type='gov')
        if field == 'summary':
            data['summary'] = {'text': text, 'evidence_ids': ['item-1']}
            path = '$.summary.text'
        else:
            data['items'][0][field] = text
            path = f'$.items[0].{field}'
        return [f['level'] for f in tool.lint_report(data)['findings'] if f['path'] == path]

    def test_factual_text_stays_clean(self):
        for field, text in (('summary', '今日2条中欧班列从赣州国际陆港发车，满载电子产品驶往汉堡，标志着线路班次进一步加密，相关企业物流成本有望下降。'),
                            ('reader_summary', '市医保局启动参保信息采集时间安排，居民可在线登记。'),
                            ('reader_summary', '市金融监管局提醒群众警惕以“高收益、稳赚不赔”为诱饵的非法集资活动。'),
                            ('reader_summary', '人民银行调查显示，三季度基础设施贷款需求有所回落。'),
                            ('reader_summary', '某基金三季度继续加仓示例股份。')):
            with self.subTest(text=text):
                self.assertNotIn('error', self.findings(field, text))

    def test_advice_and_counts_still_caught(self):
        for field, text in (('summary', '本期晨报共收录12条，其中政府发布8条。'), ('reader_analysis', '给予目标价格35元。'),
                            ('reader_analysis', '建议加仓示例股份。'), ('reader_analysis', '首次覆盖给予“强烈推荐”评级。'),
                            ('reader_summary', '建议近期上门拜访企业财务负责人。'), ('reader_summary', '该企业具备授信潜力。'),
                            ('reader_analysis', '该理财产品稳赚不赔。'), ('reader_analysis', '居民贷款需求较大，可作为营销重点。')):
            with self.subTest(text=text):
                self.assertIn('error', self.findings(field, text))

    def test_hidden_non_format_characters_rejected(self):
        for value in ('可ㅤ授信', '建议͏买入', '甲⠀乙', '甲 乙'):
            data = report()
            data['items'][0]['analysis'] = value
            with self.subTest(value=repr(value)), self.assertRaises(tool.ValidationError):
                tool.validate_report(data)

    def test_repeated_section_reported_once(self):
        data = multi_report(4)
        for item in data['items']:
            item['section'] = '政府资讯 · 营销机会'
        paths = [f['path'] for f in tool.lint_report(data)['findings'] if f['path'].endswith('.section')]
        self.assertEqual(len(paths), 1)

    def test_future_special_reports_future_not_midnight(self):
        with self.assertRaisesRegex(tool.ValidationError, '未来'):
            tool.compute_window('special', '2026-10-16T00:00:00+08:00', '2026-11-01/2026-11-30')

    def test_json_output_valid_on_ascii_console(self):
        import subprocess, os, sys
        env = dict(os.environ, PYTHONIOENCODING='ascii')
        for args in (['options'], ['-h']):
            proc = subprocess.run([sys.executable, '-B', str(SCRIPT)] + args, capture_output=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = subprocess.run([sys.executable, '-B', str(SCRIPT), 'options'], capture_output=True, env=env)
        self.assertIn('公司金融', json.loads(proc.stdout.decode('ascii'))['audiences'].values())


def pool_entry(**extra):
    entry = {'id': 'ent-1', 'name': '示例新材料股份有限公司', 'type': 'enterprise', 'public_ok': True}
    entry.update(extra)
    return entry


class JiangxiScopeTests(unittest.TestCase):
    def test_regions_default_to_jiangxi(self):
        value = tool.validate_config({'schema_version': 1, 'audience': 'corporate'})
        self.assertEqual(value['regions'], [{'name': '江西省', 'parent': '中国', 'level': 'province'}])

    def test_only_jiangxi_regions(self):
        for region in ({'name': '江苏省', 'parent': '中国', 'level': 'province'}, {'name': '苏州市', 'parent': '江苏省', 'level': 'city'},
                       {'name': '赣州市', 'parent': '中国', 'level': 'city'}, {'name': '昆山市', 'parent': '苏州市', 'level': 'county'},
                       {'name': '中国', 'parent': '', 'level': 'country'}):
            value = config()
            value['regions'] = [region]
            with self.subTest(region=region), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)
        value = config()
        value['regions'] = [{'name': name, 'parent': '江西省', 'level': 'city'} for name in tool.JIANGXI_CITIES]
        value['regions'].append({'name': '南康区', 'parent': '赣州市', 'level': 'county'})
        self.assertEqual(len(tool.validate_config(value)['regions']), 12)

    def test_bank_audiences_only(self):
        for audience in ('general', 'investment_promotion', 'media', 'headquarters'):
            value = config()
            value['audience'] = audience
            with self.subTest(audience=audience), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)


class RoleProfileTests(unittest.TestCase):
    def test_custom_role_needs_title(self):
        value = config()
        value['audience'] = 'custom'
        with self.assertRaisesRegex(tool.ValidationError, 'role_profile.title'):
            tool.validate_config(value)
        value['role_profile'] = {'title': '园区金融服务专员', 'focus_directions': ['开发区政策', '园区企业投产'],
                                 'key_questions': ['园区新增了哪些投产项目？'], 'deprioritize': ['消费活动']}
        self.assertEqual(tool.validate_config(value)['role_profile']['title'], '园区金融服务专员')

    def test_profile_limits_and_privacy(self):
        for profile in ({'focus_directions': ['a'] * 13}, {'focus_directions': ['重复', '重复']}, {'title': 'x' * 41},
                        {'focus_directions': ['我行存量客户经营']}, {'key_questions': ['该客户授信额度多少？']}, {'extra': 1}):
            value = config()
            value['role_profile'] = profile
            with self.subTest(profile=profile), self.assertRaises(tool.ValidationError):
                tool.validate_config(value)


class WatchPoolTests(unittest.TestCase):
    def check_bad(self, entries, pattern=None):
        value = config()
        value['watch_pool'] = entries
        with self.assertRaises(tool.ValidationError) as ctx:
            tool.validate_config(value)
        if pattern:
            self.assertRegex(str(ctx.exception), pattern)

    def test_valid_pool_normalises_defaults(self):
        value = config()
        value['watch_pool'] = [pool_entry(aliases=['示例新材'], region='赣州市南康区', tags=['新能源'], priority='high', identifier='91360000XXXXXXXXXX'),
                               {'id': 'park_1', 'name': '示例高新区', 'type': 'park', 'public_ok': True}]
        pool = tool.validate_config(value)['watch_pool']
        self.assertEqual(pool[1]['priority'], 'normal')
        self.assertFalse(pool[0]['include_subsidiaries'])

    def test_public_ok_must_be_true(self):
        self.check_bad([pool_entry(public_ok=False)], 'public_ok')
        entry = pool_entry()
        del entry['public_ok']
        self.check_bad([entry], 'public_ok')

    def test_ids_names_and_types(self):
        self.check_bad([pool_entry(), pool_entry(name='另一家')], 'ID重复')
        self.check_bad([pool_entry(), pool_entry(id='ent-2', aliases=['示例新材料股份有限公司'])], '碰撞')
        self.check_bad([pool_entry(id='Bad ID')], 'ID')
        self.check_bad([pool_entry(type='client')])
        self.check_bad([pool_entry(type='park', identifier='123')], '企业条目')
        self.check_bad([pool_entry(type='topic', include_subsidiaries=True)])
        self.check_bad([pool_entry(region='苏州市')], '地区')
        self.check_bad([pool_entry(priority='urgent')])

    def test_private_banking_fields_rejected(self):
        for extra in ({'note': '我行授信客户，额度5000万'}, {'tags': ['存量客户']}, {'note': '联系人张三 电话123'}, {'account_no': '6222'}):
            with self.subTest(extra=extra):
                self.check_bad([pool_entry(**extra)])


class ReportPoolTests(unittest.TestCase):
    def data(self):
        data = report()
        data['pool'] = [{'id': 'ent-1', 'name': '示例<b>新材料</b>', 'type': 'enterprise', 'priority': 'high'},
                        {'id': 'park-1', 'name': '安静的园区', 'type': 'park', 'priority': 'high'},
                        {'id': 'topic-1', 'name': '普通专题', 'type': 'topic'}]
        data['items'][0]['pool_ids'] = ['ent-1']
        data['focus'] = {'title': '公司金融岗', 'directions': ['新能源材料', '重大项目']}
        return data

    def test_pool_refs_must_exist(self):
        data = self.data()
        for refs in (['absent'], ['ent-1', 'ent-1'], [1]):
            data['items'][0]['pool_ids'] = refs
            with self.subTest(refs=refs), self.assertRaisesRegex(tool.ValidationError, 'pool_ids'):
                tool.validate_report(data)
        data = self.data()
        data['pool'].append(dict(data['pool'][0]))
        with self.assertRaisesRegex(tool.ValidationError, 'ID重复'):
            tool.validate_report(data)

    def test_pool_panel_and_badges_in_every_layout(self):
        for layout in tool.LAYOUTS:
            rendered = tool.render_report(self.data(), layout=layout)
            with self.subTest(layout=layout):
                self.assertIn('关注池动态', rendered)
                self.assertIn('关注池 · 示例&lt;b&gt;新材料&lt;/b&gt;', rendered)
                self.assertNotIn('<b>新材料</b>', rendered)
                self.assertIn('高优先级条目本期未见已核实公开动态：</b>安静的园区', rendered)
                self.assertNotIn('普通专题', rendered.split('pool-quiet', 1)[1].split('</p>', 1)[0])
                self.assertIn('关注池共3项，本期命中1项', rendered)
                self.assertIn('<b>关注方向</b><span>新能源材料、重大项目</span>', rendered)
                self.assertIn('岗位画像：公司金融岗', rendered)

    def test_no_pool_no_panel(self):
        self.assertNotIn('关注池动态', tool.render_report(report()))


class PoolImportTests(unittest.TestCase):
    def run_import(self, content):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / 'pool.csv', Path(root) / 'pool.json'
            source.write_text(content, encoding='utf-8-sig')
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = tool.main(['pool-import', '--csv', str(source), '--out', str(target)])
            return code, (json.loads(target.read_text(encoding='utf-8')) if target.exists() else None), err.getvalue()

    def test_template_round_trips(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 't.csv'
            self.assertEqual(tool.main(['pool-template', '--out', str(target)]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(tool.main(['pool-template', '--out', str(target)]), 2)
            code, data, _ = self.run_import(target.read_text(encoding='utf-8-sig'))
        self.assertEqual(code, 0)
        self.assertEqual([e['type'] for e in data['watch_pool']], ['enterprise', 'park'])
        self.assertEqual(data['watch_pool'][0]['aliases'], ['示例新材', '示例股份'])
        self.assertEqual(data['watch_pool'][0]['priority'], 'high')

    def test_english_headers_and_generated_ids(self):
        code, data, _ = self.run_import('name,type,region\n甲公司,enterprise,九江市\n乙园区,园区,\n')
        self.assertEqual(code, 0)
        self.assertEqual([e['id'] for e in data['watch_pool']], ['pool-001', 'pool-002'])

    def test_errors_name_csv_row(self):
        code, data, err = self.run_import('名称,类型,备注\n甲公司,企业,\n乙公司,企业,我行授信客户\n')
        self.assertEqual(code, 2)
        self.assertIsNone(data)
        self.assertIn('CSV第3行', err)
        code, _, err = self.run_import('名称\n甲公司\n')
        self.assertIn('类型', err)

class ExampleFileTests(unittest.TestCase):
    def test_example_configs_validate(self):
        paths = sorted((ROOT / 'examples').glob('config-*.json'))
        self.assertGreaterEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path.name):
                tool.validate_config(tool.load_json(path))

    def test_example_reports_render_and_have_no_lint_errors(self):
        paths = sorted((ROOT / 'examples').glob('report-*.json')) + sorted((ROOT / 'examples' / 'gallery').glob('*.json'))
        self.assertGreaterEqual(len(paths), 4)
        for path in paths:
            data = tool.load_json(path)
            with self.subTest(path=path.name):
                self.assertIn('<main class="page">', tool.render_report(data))
                self.assertEqual(tool.lint_report(data)['errors'], 0)

    def test_gallery_is_clearly_fictional(self):
        for path in (ROOT / 'examples' / 'gallery').glob('*.json'):
            data = tool.load_json(path)
            with self.subTest(path=path.name):
                self.assertIn('虚构', data['title'] + data['config_summary'])
                for item in data['items']:
                    for source in item['sources']:
                        self.assertRegex(source['url'], r'^https://example\.(com|org|net)/')

if __name__ == '__main__':
    unittest.main()
