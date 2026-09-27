"""
统一环境变量加载模块。

查找优先级：
  1. CLI 显式值
  2. 系统环境变量（os.environ）
  3. 项目级 .env
  4. 用户级 ~/.config/courseware-studio/.env

本 skill 不读取其它 skill 的私有配置目录。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _script_utils import decode_text_blob, is_inside  # noqa: E402


def _strip_env_comment(value):
    """剥掉 .env 值里的行内注释与成对引号（引号感知）。

    规则（对齐 dotenv 的常见行为）：
      · 值以引号开头：取到配对的闭合引号为止——"sk-a#b" 里的 # 是值的一部分，
        闭合引号之后的任意内容按注释丢弃；没有闭合引号时保守地保留引号后的
        全部正文（宁可把注释当值，也不要把密钥截断一半）。
      · 值不带引号：首个"空白 + #"（或值直接以 # 开头）处截断；
        紧贴的非空白 #（如锚点、abc#def）视为值的一部分。
    """
    s = value
    if s[:1] in ('"', "'"):
        q = s[0]
        i = 1
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == q:
                return s[1:i]
            i += 1
        return s[1:]
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "#" and (i == 0 or s[i - 1].isspace()):
            break
        i += 1
    return s[:i].rstrip()


# 用户级 .env 路径
_USER_ENV_PATH = os.path.join(os.path.expanduser("~"), ".config", "courseware-studio", ".env")
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"

_ENV_CACHE = {}  # path -> 解析结果（.env 在单次 CLI 进程内稳定，缓存避免每次调用重复 3 编码探测）


def _candidate_project_envs(source_path=None):
    """返回项目级 .env 候选，从 source 所在目录向上、到当前工作目录边界即停。

    候选顺序：source_path 所在目录及其在 cwd 之内的祖先，最后 cwd 本身。
    向上爬升的停止边界：起点自身永远作为候选（它是调用方明确指定的位置）；
    祖先目录只有仍在 cwd 之内才是候选，且家目录永远不算项目候选（用户级
    密钥由 _USER_ENV_PATH 专管，否则 ~/.env 会以项目优先级压过
    ~/.config/courseware-studio/.env）。命中 .git 项目根、到达 cwd、到达
    家目录或触到盘根即停。

    仅读取文件，不把 .env 复制进 Artifact。
    """
    candidates = []
    seen = set()

    def add(path):
        path = os.path.abspath(path)
        env_path = os.path.join(path, ".env")
        if env_path not in seen:
            seen.add(env_path)
            candidates.append(env_path)

    roots = []
    if source_path:
        roots.append(os.path.dirname(os.path.abspath(source_path)))
    roots.append(os.path.abspath(os.getcwd()))

    cwd_abs = os.path.normcase(os.path.abspath(os.getcwd()))
    # normcase：Windows 上盘符大小写（C:\ vs c:\）不该让"是不是 home/cwd"翻脸。
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    for root in roots:
        cur = root
        while True:
            is_start = cur == root
            cur_n = os.path.normcase(cur)
            # 家目录永远不是项目候选——即便它恰好就是起点（source 直接放在
            # ~ 下），否则 ~/.env 会以项目优先级压过 ~/.config/…/.env。
            if cur_n != home and (is_start or is_inside(cur, cwd_abs)):
                add(cur)
            # 祖先一旦越出 cwd（或爬到 home）就不再向上：起点在 cwd 之外时
            # 只取起点本身这一个候选。
            if not is_start and (not is_inside(cur, cwd_abs) or cur_n == home):
                break
            if (os.path.dirname(cur) == cur or cur_n == cwd_abs or cur_n == home
                    or os.path.exists(os.path.join(cur, ".git"))):
                break
            cur = os.path.dirname(cur)

    return candidates


def find_project_env(source_path=None):
    """返回第一个存在的项目级 .env 路径，否则 None。"""
    for path in _candidate_project_envs(source_path=source_path):
        if os.path.isfile(path):
            return path
    return None


def _parse_env_file(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict（带进程内缓存）。"""
    if path in _ENV_CACHE:
        return _ENV_CACHE[path]
    result = _parse_env_file_raw(path)
    _ENV_CACHE[path] = result
    return result


def _parse_env_file_raw(path):
    """解析一个 KEY=VALUE 格式的 .env 文件，返回 dict。

    编码探测走 _script_utils.decode_text_blob（全仓唯一一份；与 build_page /
    check_gates 同一条链）。UnicodeDecodeError 若在 get_key()/load_env() 内部
    裸栈穿透，命令行 dry-run 会一起瘫痪且报错不指向"编码问题"——这里探测失败
    只 warn-and-skip。高概率触发路径：PowerShell 5.1 重定向 `>` 产出 UTF-16；
    记事本把含中文注释的 .env 存成 ANSI (GBK)。
    """
    result = {}
    if not os.path.isfile(path):
        return result
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError as e:
        print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
        return result
    text = decode_text_blob(blob)
    if text is None:
        print(f"[warn] 无法读取 {path}: 不是可识别的文本编码"
              f"(尝试过 utf-8 / utf-16 / gb18030；含 NUL 的无 BOM UTF-16 也归这里)。"
              f"请用 UTF-8 重新保存该文件（记事本另存为右下角选 UTF-8）",
              file=sys.stderr)
        return result
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            # 兼容 shell 习惯写法（export KEY=VALUE）：
            # key 上的 export 前缀剥掉，否则查表永远 miss
            if k.startswith("export "):
                k = k[len("export "):].strip()
            v = v.strip()
            # 引号感知地剥行内注释与成对引号（"sk-xxx" / sk-xxx # 注释）：
            # 原样读入会把引号或注释一起带给 OpenAI SDK，得到 401 且
            # 报错不指向真正原因。
            v = _strip_env_comment(v)
            if k:
                result[k] = v
    return result


def load_env(source_path=None):
    """按优先级合并项目级、用户级与进程环境变量。

    合并顺序即优先级：os.environ > 项目 .env > 用户 .env（后者覆盖前者）。
    CLI 显式值在最外层由 ``cli or load_env(...).get(name)`` 补足，构成
    CLI > os.environ > 项目 .env > 用户 .env 的完整链。
    """
    merged = {}
    for src in (_parse_env_file(_USER_ENV_PATH),
                _parse_env_file(find_project_env(source_path=source_path) or ""),
                os.environ):
        for k, v in src.items():
            # 空值不参与合并：项目 .env 里留空的占位行不该遮住用户级有效配置，
            # 逐层"空=没配、继续向下找"的语义与 get_key 的短路链一致。
            if v:
                merged[k] = v
    return merged


def get_key(name, cli_value=None, source_path=None):
    """获取单个密钥：CLI > load_env 合并链。"""
    return cli_value or load_env(source_path=source_path).get(name) or None


def resolve_model_config(cli_model, cli_base_url, model_env_name, default_model,
                         source_path=None):
    """按 CLI > 环境变量 > 项目 .env > 用户 .env > 默认值解析模型配置。"""
    env = load_env(source_path=source_path)
    model = cli_model or env.get(model_env_name) or default_model
    base_url = cli_base_url or env.get("MIMO_BASE_URL") or DEFAULT_BASE_URL
    return model, base_url
