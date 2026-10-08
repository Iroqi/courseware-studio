"""_env：.env 解析、候选爬升、合并优先级。"""
import os

import pytest

import _env as env


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.setattr(env, "_ENV_CACHE", {})


class TestStripEnvComment:
    def test_quoted_value_keeps_hash(self):
        assert env._strip_env_comment('"sk-a#b"') == "sk-a#b"

    def test_quoted_value_drops_trailing_comment(self):
        assert env._strip_env_comment('"sk-abc" # comment') == "sk-abc"

    def test_unclosed_quote_keeps_rest(self):
        # 没有闭合引号时保守保留，宁把注释当值也不截断密钥
        assert env._strip_env_comment('"sk-abc') == "sk-abc"

    def test_unquoted_comment_cut(self):
        assert env._strip_env_comment("sk-abc # note") == "sk-abc"

    def test_unquoted_leading_hash_cut(self):
        assert env._strip_env_comment("#comment") == ""

    def test_tight_hash_kept(self):
        # 紧贴的非空白 # 是值的一部分（锚点 / abc#def）
        assert env._strip_env_comment("abc#def") == "abc#def"

    def test_quoted_value_drops_after_closing(self):
        assert env._strip_env_comment('"a#b" tail#x') == "a#b"


class TestParseEnvFileRaw:
    def test_basic(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("A=1\nB=hello world\n", encoding="utf-8")
        assert env._parse_env_file_raw(str(p)) == {"A": "1", "B": "hello world"}

    def test_export_prefix_stripped(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("export KEY=value\n", encoding="utf-8")
        assert env._parse_env_file_raw(str(p)) == {"KEY": "value"}

    def test_comments_and_blanks(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("# comment\n\nA=1 # inline\n", encoding="utf-8")
        assert env._parse_env_file_raw(str(p)) == {"A": "1"}

    def test_quoted_value(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text('K="sk-x" # note\n', encoding="utf-8")
        assert env._parse_env_file_raw(str(p)) == {"K": "sk-x"}

    def test_utf16_with_bom(self, tmp_path):
        p = tmp_path / ".env"
        p.write_bytes("K=中文字符".encode("utf-16"))
        assert env._parse_env_file_raw(str(p)) == {"K": "中文字符"}

    def test_missing_file(self, tmp_path):
        assert env._parse_env_file_raw(str(tmp_path / "nope.env")) == {}


class TestCandidateProjectEnvs:
    def test_start_dir_always_candidate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cands = env._candidate_project_envs(source_path=str(tmp_path / "sub" / "x.json"))
        assert str(tmp_path / "sub" / ".env") in cands

    def test_stops_at_git_root(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        cands = env._candidate_project_envs(source_path=str(sub / "x.json"))
        # a 与 a/b 在 .git 之下；再往上到 tmp_path（含 .git）即停，不再出 tmp_path
        assert str(sub / ".env") in cands
        assert str(tmp_path / "a" / ".env") in cands
        assert str(tmp_path / ".env") in cands
        assert len(cands) == 3


class TestLoadEnv:
    def test_priority_env_over_project_over_user(self, tmp_path, monkeypatch):
        user_env = tmp_path / "user.env"
        proj_env = tmp_path / "proj" / ".env"
        proj_env.parent.mkdir()
        user_env.write_text("A=user\nB=user\n", encoding="utf-8")
        proj_env.write_text("B=proj\nC=proj\n", encoding="utf-8")
        monkeypatch.setattr(env, "_USER_ENV_PATH", str(user_env))
        monkeypatch.setattr(env, "find_project_env",
                            lambda source_path=None: str(proj_env))
        monkeypatch.setenv("C", "env")
        merged = env.load_env()
        assert merged["A"] == "user"
        assert merged["B"] == "proj"      # 项目覆盖用户
        assert merged["C"] == "env"       # 进程环境覆盖项目

    def test_empty_value_does_not_shadow(self, tmp_path, monkeypatch):
        user_env = tmp_path / "user.env"
        proj_env = tmp_path / "proj" / ".env"
        proj_env.parent.mkdir()
        user_env.write_text("K=good\n", encoding="utf-8")
        proj_env.write_text("K=\n", encoding="utf-8")
        monkeypatch.setattr(env, "_USER_ENV_PATH", str(user_env))
        monkeypatch.setattr(env, "find_project_env",
                            lambda source_path=None: str(proj_env))
        merged = env.load_env()
        assert merged["K"] == "good"


class TestResolveModelConfig:
    def test_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(env, "_USER_ENV_PATH", str(tmp_path / "u.env"))
        monkeypatch.setattr(env, "find_project_env", lambda source_path=None: None)
        model, base = env.resolve_model_config(None, None)
        assert model == env.DEFAULT_MODEL
        assert base == env.DEFAULT_BASE_URL

    def test_cli_wins(self, tmp_path, monkeypatch):
        monkeypatch.setattr(env, "_USER_ENV_PATH", str(tmp_path / "u.env"))
        monkeypatch.setattr(env, "find_project_env", lambda source_path=None: None)
        model, base = env.resolve_model_config("custom", "http://x")
        assert model == "custom"
        assert base == "http://x"
