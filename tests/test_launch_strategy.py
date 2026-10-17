"""浏览器启动口径的三档退路（launch strategy）。

真实 Chrome 的行为按机器差别很大，所以这里用一个 **fake chrome 脚本**把三类
故障各自复现一遍：fake 只看自己拿到的 argv 与环境，决定"拒绝启动 / 挂死 / 成功"。
真实 Chrome 只在最后一组用例里用来确认"至少有一档能过"（没有 Chrome 就跳过）。

守住的契约：
  1. 带沙箱失败 → 退 --no-sandbox；再失败 → 退"隔离 HOME 且不传 --user-data-dir"；
  2. 三档全失败才返回 None（旧代码只退一档，root 容器里浏览器冒烟整段没跑却绿灯）；
  3. 探测用**一次性转储**本身（--dump-dom），且绝不带 --allow-file-access-from-files；
  4. 口径进程内缓存（33 帧导出不该每帧重跑探测），且缓存里不存路径——
     "每帧一个新 profile"必须由每帧自己建目录。
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

import check_gates as cg

FAKE = """#!/usr/bin/env python3
# fake chrome：按 argv 与环境决定成败，行为由 FAKE_MODE 指定。
import os, sys, time

argv = sys.argv[1:]
mode = os.environ['FAKE_MODE']
dump = '--dump-dom' in argv or any(a.startswith('--screenshot') for a in argv)
udd = [a for a in argv if a.startswith('--user-data-dir')]
home = os.environ.get('HOME', '')

if mode == 'never':
    sys.exit(9)
if mode == 'need-no-sandbox' and '--no-sandbox' not in argv:
    sys.exit(1)                      # root 容器里带沙箱那一档的真实形态
if mode == 'hang-on-udd':
    if udd and dump:
        time.sleep(30)               # 本沙箱实测：新配置目录的一次性转储永不归
        sys.exit(2)
    if '--no-sandbox' not in argv:
        sys.exit(1)
if mode == 'isolated-only':
    # 只要 --user-data-dir 在场就挂死；隔离档（不带它、HOME 落在临时目录）才通
    if udd:
        time.sleep(30)
        sys.exit(3)
    if '--no-sandbox' not in argv:
        sys.exit(1)
    if not os.path.realpath(home).startswith(os.environ['FAKE_TMP']):
        sys.exit(4)
body = 'nothing here' if mode == 'wrong-output' else 'courseware-preflight-ok'
sys.stdout.write('<html><body>%s</body></html>' % body)
sys.stdout.flush()
sys.exit(0)
"""


@pytest.fixture
def fake_chrome(tmp_path, monkeypatch):
    """把 fake 脚本落成一个可执行文件，并把"临时目录前缀"告诉它。"""
    script = tmp_path / 'fake-chrome'
    script.write_text(FAKE, encoding='utf-8')
    script.chmod(0o755)
    monkeypatch.setenv('FAKE_TMP', str(Path(tempfile.gettempdir()).resolve()) + os.sep)
    cg._LAUNCH_STRATEGY_CACHE.clear()
    return script


def _strategy(monkeypatch, fake_chrome, mode, timeout=3):
    monkeypatch.setenv('FAKE_MODE', mode)
    return cg._browser_launch_strategy(str(fake_chrome), timeout=timeout)


class TestLaunchLadder:
    def test_prefers_sandboxed_when_it_works(self, monkeypatch, fake_chrome):
        """第一档能过就不该出现任何降级 flag：桌面与 CI 的正常路径不变。"""
        assert _strategy(monkeypatch, fake_chrome, 'ok') == {
            'extra_flags': [], 'isolated_home': False}

    def test_falls_back_to_no_sandbox(self, monkeypatch, fake_chrome):
        """带沙箱起不来 → 退 --no-sandbox，仍走 --user-data-dir。"""
        assert _strategy(monkeypatch, fake_chrome, 'need-no-sandbox') == {
            'extra_flags': ['--no-sandbox'], 'isolated_home': False}

    def test_falls_back_to_isolated_home(self, monkeypatch, fake_chrome):
        """只要 --user-data-dir 就挂死的环境 → 第三档必须**不传**它。"""
        assert _strategy(monkeypatch, fake_chrome, 'isolated-only') == {
            'extra_flags': ['--no-sandbox'], 'isolated_home': True}

    def test_two_hang_rungs_then_land_on_third(self, monkeypatch, fake_chrome):
        """复现本沙箱的真实形态：前两档挂在 --user-data-dir 上（各吃一个 timeout），
        第三档绕开它就通。旧代码到第二档就报"预检未通过"，浏览器冒烟静默没跑
        却照样绿灯——这条用例是那条漏检的回归闸。"""
        t0 = time.monotonic()
        assert _strategy(monkeypatch, fake_chrome, 'hang-on-udd', timeout=3) == {
            'extra_flags': ['--no-sandbox'], 'isolated_home': True}
        # 两个 timeout 都要真的被用上（挂死不是瞬间失败），但整体别把 CI 拖没
        assert 5 < time.monotonic() - t0 < 60

    def test_all_rungs_fail_returns_none(self, monkeypatch, fake_chrome):
        """三档全失败才说"预检未通过"：返回 None，调用方走降级报告通道。"""
        assert _strategy(monkeypatch, fake_chrome, 'never') is None

    def test_zero_rc_with_wrong_dump_counts_as_failure(self, monkeypatch, fake_chrome):
        """转储内容不含探针文本就判失败：不能"进程起来了就算过"。"""
        assert _strategy(monkeypatch, fake_chrome, 'wrong-output') is None


class TestStrategyContract:
    def test_cache_keyed_by_chrome_path(self, monkeypatch, fake_chrome):
        """同一 chrome 只探测一次：33 帧导出 × 3 档 = 99 次挂死不可接受。"""
        monkeypatch.setenv('FAKE_MODE', 'ok')
        first = time.monotonic()
        cg._browser_launch_strategy(str(fake_chrome), timeout=8)
        span_first = time.monotonic() - first
        second = time.monotonic()
        cg._browser_launch_strategy(str(fake_chrome), timeout=8)
        assert time.monotonic() - second < span_first
        assert cg._LAUNCH_STRATEGY_CACHE[str(fake_chrome)] == {
            'extra_flags': [], 'isolated_home': False}

    def test_cache_stores_no_paths(self, monkeypatch, fake_chrome):
        """口径决定里只有 flag 与档位布尔：路径每帧自建，缓存不能带旧目录。"""
        strat = _strategy(monkeypatch, fake_chrome, 'isolated-only')
        assert set(strat) == {'extra_flags', 'isolated_home'}

    def test_launch_env_refuses_missing_home_on_isolated_rung(self, monkeypatch, fake_chrome):
        """隔离档忘了给 HOME 要当场报错，而不是悄悄退回系统 HOME 再挂死一次。"""
        strat = _strategy(monkeypatch, fake_chrome, 'isolated-only')
        with pytest.raises(ValueError):
            cg._launch_env(strat, None)

    def test_launch_env_returns_none_for_normal_rungs(self):
        """常规档继承进程环境：返回 None 交给 subprocess，不自己拼一份污染环境。"""
        assert cg._launch_env({'extra_flags': [], 'isolated_home': False},
                              Path('/nonexistent')) is None
        assert cg._launch_env({'extra_flags': ['--no-sandbox'],
                               'isolated_home': False}, None) is None

    def test_probe_never_allows_file_access(self, monkeypatch, fake_chrome):
        """探针自身是安全防线：绝不开 --allow-file-access-from-files（那等于给
        不可信页面开任意本地文件读取通道，含 .env）。"""
        seen = []
        real_popen = subprocess.Popen

        class Spy:
            def __init__(self, cmd, *a, **kw):
                seen.append(list(cmd))
                self._p = real_popen(cmd, *a, **kw)

            def __getattr__(self, name):
                return getattr(self._p, name)

        monkeypatch.setattr(cg.subprocess, 'Popen', Spy)
        _strategy(monkeypatch, fake_chrome, 'isolated-only')
        assert len(seen) == 3, f'三档应各探测一次，实际 {len(seen)} 次'
        for cmd in seen:
            assert all('--allow-file-access-from-files' not in a for a in cmd)
        # 前两档必须真的带 --user-data-dir，第三档必须不带——这是三档的唯一区别
        assert [any(a.startswith('--user-data-dir') for a in c) for c in seen] == [
            True, True, False]

    def test_candidates_are_ordered_least_intrusive_first(self):
        """三档顺序即口径：带沙箱 → --no-sandbox → 隔离 HOME，不能倒过来。"""
        cands = cg._launch_candidates(10)
        assert [c['extra_flags'] for c in cands] == [[], ['--no-sandbox'], ['--no-sandbox']]
        assert [c['isolated_home'] for c in cands] == [False, False, True]
        assert all(c['timeout'] == 10 for c in cands)


class TestPreflightNote:
    def test_normal_rung_stays_silent(self):
        assert cg._preflight_note({'extra_flags': [], 'isolated_home': False}) == ''

    def test_no_sandbox_rung_reports_why(self):
        note = cg._preflight_note({'extra_flags': ['--no-sandbox'], 'isolated_home': False})
        assert 'no-sandbox' in note and 'user namespaces' in note

    def test_isolated_rung_reports_the_hang(self):
        note = cg._preflight_note({'extra_flags': ['--no-sandbox'], 'isolated_home': True})
        assert '隔离 HOME' in note and '--user-data-dir' in note

    def test_note_carries_no_print_prefix(self):
        """note 只写原因、不带 `[note]`：前缀由打印侧统一加，--json 里才是纯文本。"""
        for strat in ({'extra_flags': ['--no-sandbox'], 'isolated_home': False},
                      {'extra_flags': ['--no-sandbox'], 'isolated_home': True}):
            assert not cg._preflight_note(strat).startswith('[note]')

    def test_none_strategy_produces_no_note(self):
        assert cg._preflight_note(None) == ''


@pytest.mark.skipif(not cg._find_chrome(), reason='需要 Chrome/Edge')
class TestRealChromeResolvesAStrategy:
    def test_real_browser_lands_on_some_rung(self):
        """真实浏览器必须至少过一档：三档全挂是环境问题，"过了一档却报预检未通过"
        是代码问题——这里只锁后者不发生。timeout 收紧到 6s：本沙箱里前两档真的
        会各挂满一个 timeout（那正是第三档存在的理由），别让它拖慢整套。"""
        chrome = cg._find_chrome()
        strat = cg._browser_launch_strategy(chrome, timeout=6)
        assert strat is not None, f'{chrome} 三档退路全未通过'
        assert set(strat) == {'extra_flags', 'isolated_home'}

    def test_isolated_home_env_targets_the_temp_home_only(self, tmp_path):
        """隔离档只改子进程的 HOME/USERPROFILE，绝不写回当前进程环境——
        否则 .env 查找链与用户配置目录会被一次交付检查悄悄换地方。"""
        home = tmp_path / 'home'
        home.mkdir()
        before = dict(os.environ)
        env = cg._isolated_home_env(home)
        assert env['HOME'] == str(home) and env['USERPROFILE'] == str(home)
        assert os.environ == before
