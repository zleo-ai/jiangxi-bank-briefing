#!/usr/bin/env python3
"""按需将最终证据JSON绘制为含中文正文与图表的本地PNG长图。"""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
from briefing_tool import LAYOUTS, THEMES, ValidationError, configure_streams, load_json, render_report, stdout_is_utf8

MAX_RENDER_PIXELS = 90_000_000
MAX_RENDER_HEIGHT = 60_000


def export_png(data_path, out_path, width=540, scale=2, browser_channel='auto', theme=None, layout=None, executable_path=None):
    """不请求网络，不运行页面脚本，不覆盖已有文件，不截断内容；长图始终使用浅色配色。"""
    if type(width) is not int or not 360 <= width <= 720:
        raise ValidationError('$.width: 必须是360至720的整数')
    if type(scale) is not int or scale not in (1, 2, 3):
        raise ValidationError('$.scale: 必须是1、2或3')
    if browser_channel not in ('auto', 'chromium', 'chrome', 'msedge'):
        raise ValidationError('$.browser_channel: 不支持的浏览器')
    if theme is not None and theme not in THEMES:
        raise ValidationError('$.theme: 不支持的配色')
    if layout is not None and layout not in LAYOUTS:
        raise ValidationError('$.layout: 不支持的版式')
    if executable_path is not None and not Path(executable_path).is_file():
        raise ValidationError('$.executable_path: 指定的本地浏览器不存在')
    target = Path(out_path)
    if target.suffix.lower() != '.png':
        raise ValidationError('$.out: 输出扩展名必须为.png')
    if target.exists() or target.is_symlink():
        raise ValidationError('$.out: 输出文件已存在，禁止覆盖')
    if not target.parent.is_dir():
        raise ValidationError('$.out: 输出目录不存在，请先选择已有目录')
    data = load_json(data_path)
    rendered = render_report(data, theme, layout, 'light')
    if not data['items']:
        raise ValidationError('$.items: 没有已核实事项，不生成空白分享长图')
    if not data.get('summary'):
        raise ValidationError('$.summary: 先编制并核实内容摘要，再导出分享长图')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ValidationError('$.runtime: PNG导出需可用的Playwright及Chromium、Chrome或Edge；HTML生成无需此依赖') from None
    channels = (None, 'chrome', 'msedge') if browser_channel == 'auto' else (None if browser_channel == 'chromium' else browser_channel,)
    launches = [{'executable_path': str(executable_path)}] if executable_path else [{'channel': channel} for channel in channels]
    browser = None
    with sync_playwright() as playwright:
        for options in launches:
            try:
                browser = playwright.chromium.launch(headless=True, **options)
                break
            except Exception:
                continue
        if browser is None:
            raise ValidationError('$.runtime: 未找到可启动的浏览器，请准备Playwright Chromium或已安装的Chrome/Edge')
        try:
            context = browser.new_context(
                viewport={'width': width, 'height': 900},
                device_scale_factor=scale,
                java_script_enabled=False,
                service_workers='block',
                locale='zh-CN',
                timezone_id='Asia/Shanghai',
                color_scheme='light',
                reduced_motion='reduce',
            )
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_content(rendered, wait_until='load', timeout=30000)
            page.evaluate("document.body.classList.add('poster'); document.querySelectorAll('details').forEach(node => node.open = false)")
            page.evaluate('document.fonts.ready')
            geometry = page.evaluate('''() => ({
                width: document.documentElement.scrollWidth,
                height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
                viewport: window.innerWidth
            })''')
            if geometry['width'] > geometry['viewport'] + 1:
                raise ValidationError('$.layout: 发现横向溢出，先修复版式再导出')
            height_pixels = geometry['height'] * scale
            if height_pixels > MAX_RENDER_HEIGHT or geometry['width'] * scale * height_pixels > MAX_RENDER_PIXELS:
                raise ValidationError('$.layout: 长图超过安全绘制尺寸，降低scale或拆分报告后重试；未截断或写出图片')
            image = page.screenshot(full_page=True, type='png', animations='disabled', timeout=60000)
            if image[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValidationError('$.out: 浏览器未生成有效PNG')
            actual_width, actual_height = struct.unpack('>II', image[16:24])
            if actual_height < height_pixels:
                raise ValidationError('$.out: 图片高度不足，拒绝写出截断的长图')
            with target.open('xb') as output:
                output.write(image)
        finally:
            browser.close()
    return {
        'format': 'png', 'width': actual_width, 'height': actual_height,
        'items': len(data['items']), 'contains': '报头、覆盖说明、内容摘要、概况图表、关键数字、全部可见事实、专题与后续关注',
        'evidence_details': '折叠；完整证据仍保留于HTML和JSON',
        'network': 'blocked', 'page_scripts': 'disabled',
    }


def main(argv=None):
    configure_streams()
    parser = argparse.ArgumentParser(description='按用户要求导出本地PNG长图，HTML页面不含导出按钮')
    parser.add_argument('--data', required=True, help='已核实的04-final.json')
    parser.add_argument('--out', required=True, help='不存在的.png路径')
    parser.add_argument('--width', type=int, default=540, help='CSS宽度，360至720；默认540')
    parser.add_argument('--scale', type=int, choices=(1, 2, 3), default=2)
    parser.add_argument('--browser-channel', choices=('auto', 'chromium', 'chrome', 'msedge'), default='auto')
    parser.add_argument('--executable-path', help='可选：本机已安装的Chromium/Chrome/Edge可执行文件路径')
    parser.add_argument('--theme', choices=tuple(THEMES), help='配色，默认取报告presentation或classic')
    parser.add_argument('--layout', choices=tuple(LAYOUTS), help='版式，默认取报告presentation或standard')
    args = parser.parse_args(argv)
    try:
        result = export_png(args.data, args.out, args.width, args.scale, args.browser_channel, args.theme, args.layout, args.executable_path)
        print(json.dumps(result, ensure_ascii=not stdout_is_utf8()))
        return 0
    except (ValidationError, OSError):
        # 路径与浏览器底层报错可能带用户目录，只输出受控诊断。
        exc = sys.exc_info()[1]
        message = str(exc) if isinstance(exc, ValidationError) else '$.out: 读取或写出失败，请检查路径和权限'
        print(message, file=sys.stderr)
        return 2
    except Exception:
        print('$.render: PNG绘制失败，未宣称导出成功；检查本地浏览器环境后重试', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
