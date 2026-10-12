#!/usr/bin/env python3
"""flowkit：AI 图文素材流程工具（规格定义 v0.4）

子命令：
  validate FLOW                 检查 flow.json 是否符合规格定义（D1～D17）
  render   FLOW [-o spec.md]    生成人读的《流程规格》：流程图、完整提示词、槽位表、连接规则
  plan     FLOW [--input F | --transfer] [--run-dir D] [-o plan.md]
                                试跑前预览：每一次模型调用实际会发送的提示词和图片
  run      FLOW [--input F | --transfer] [--out DIR] [--stop-before-images] [--until STEP] [--no-pause]
  run      --resume RUN_DIR [--redo STEP] [...]
                                按流程真实调用模型，结果写入运行目录
  check-api                     测试 Base URL / API Key / 文字模型是否可用
  init-env                      在当前目录生成 flowkit.env 配置模板

只依赖 Python 3.8+ 标准库；compose / overlay_text 两个程序操作需要 Pillow。
"""
import argparse
import base64
import concurrent.futures
import copy
import datetime
import html
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SPEC_VERSION = "0.4"
MAX_IMAGES_PER_CALL = 5      # G4
MAX_OUTPUT_IMAGES = 14       # G5
MAX_PRESETS = 3              # G3
MAX_RETRY = 2                # G8
DEFAULT_IMAGE_MODEL = "gpt-image-2"

# gpt-image 系列接受的尺寸。image_size 会原样透传给接口，写错会被接口拒绝（HTTP 400）。
# 比例标注按宽:高实际计算，不要凭印象写。
IMAGE_SIZES = {
    "1024x1024": "1:1 方图",
    "1024x1536": "2:3 竖版",
    "1536x1024": "3:2 横版",
}

TYPE_NAMES = {"text": "文字模型调用", "image": "生图模型调用", "code": "程序处理"}
ROLE_NAMES = {
    "cover": "封面", "overview": "总览页", "detail": "正文页", "summary": "汇总页",
    "panel": "单格图", "comic": "多格漫画", "sticker": "表情包", "grid": "合集图",
    "avatar": "头像", "wallpaper": "壁纸", "compare": "对比图", "asset": "资产图",
}
LOOP_CTX = {"item", "group", "index", "total"}


class FlowError(Exception):
    """给用户看的错误，消息用中文写清楚原因和改法。"""


# ---------------------------------------------------------------- 基础工具

def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def split_lines(text):
    """G1：按换行拆分，去首尾空格，忽略空行。"""
    if text is None:
        return []
    if isinstance(text, list):
        text = "\n".join(str(x) for x in text)
    return [ln.strip() for ln in str(text).splitlines() if ln.strip()]


NUMBERING_RE = re.compile(r"^(?:[-*•·]\s*|第?\d{1,2}\s*[条项点页]?\s*[.、)）:：]\s*)")


def strip_fences(text):
    text = text.strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", text, re.S)
    return m.group(1).strip() if m else text


def is_img(v):
    return isinstance(v, dict) and "__img__" in v


def img_ref(path):
    return {"__img__": path}


def fmt_range(r):
    if r is None:
        return "?"
    lo, hi = r
    return str(lo) if lo == hi else f"{lo}～{hi}"


def normalize_prompt(step, flow_dir):
    """prompt 可以是字符串、字符串数组，或用 prompt_file 指向文本文件。"""
    if "prompt_file" in step:
        with open(os.path.join(flow_dir, step["prompt_file"]), encoding="utf-8") as f:
            return f.read().rstrip("\n")
    p = step.get("prompt", "")
    return "\n".join(p) if isinstance(p, list) else p


def parse_consume(s):
    s = (s or "once").strip()
    m = re.fullmatch(r"grouped\s*[\(:]\s*(\d+)\s*\)?", s)
    if m:
        return "grouped", int(m.group(1))
    if s in ("once", "per_item", "whole_list"):
        return s, None
    raise FlowError(f"consume 写法不认识：{s}（应为 once / per_item / grouped(k) / whole_list）")


def parse_ref_mode(s):
    if not s:
        return None, None
    m = re.fullmatch(r"anchor\s*\(\s*(\d+)\s*\)", s.strip())
    if m:
        return "anchor", int(m.group(1))
    if s in ("fixed", "chain"):
        return s, None
    raise FlowError(f"ref_mode 写法不认识：{s}（应为 fixed / anchor(n) / chain）")


def load_flow(path):
    flow = read_json(path)
    flow["_dir"] = os.path.dirname(os.path.abspath(path))
    flow["_path"] = os.path.abspath(path)
    for st in flow.get("steps", []):
        if st.get("type") in ("text", "image"):
            st["_prompt"] = normalize_prompt(st, flow["_dir"])
    return flow


# ---------------------------------------------------------------- 配置与 API

def load_env_file(*dirs):
    """读取 flowkit.env（KEY=VALUE），不覆盖已存在的环境变量。"""
    for d in dirs:
        p = os.path.join(d, "flowkit.env")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def normalize_base_url(url):
    """路径为空时补 /v1；带了路径（如 /v1、/api/openai/v1）就原样使用。"""
    url = url.strip().rstrip("/")
    if urllib.parse.urlparse(url).path in ("", "/"):
        url += "/v1"
    return url


class Api:
    def __init__(self, flow=None, text_model=None, image_model=None):
        base = os.environ.get("FLOW_BASE_URL")
        key = os.environ.get("FLOW_API_KEY")
        if not base or not key:
            raise FlowError("缺少 FLOW_BASE_URL 或 FLOW_API_KEY。请在 flowkit.env 里填写，或设为环境变量。")
        models = (flow or {}).get("models", {})
        self.base = normalize_base_url(base)
        self.key = key
        self.text_model = text_model or os.environ.get("FLOW_TEXT_MODEL") or models.get("text")
        self.image_model = (image_model or os.environ.get("FLOW_IMAGE_MODEL")
                            or models.get("image") or DEFAULT_IMAGE_MODEL)
        self.timeout = int(os.environ.get("FLOW_TIMEOUT", "300"))

    def _post(self, path, body, content_type):
        req = urllib.request.Request(self.base + path, data=body, method="POST")
        req.add_header("Authorization", "Bearer " + self.key)
        req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            raise FlowError(f"接口 {path} 返回 HTTP {e.code}：{detail}")
        except urllib.error.URLError as e:
            raise FlowError(f"连不上 {self.base}{path}：{e.reason}")

    def chat(self, parts):
        """parts：[("text", str) | ("image", path)]，按顺序组成一条用户消息。"""
        if not self.text_model:
            raise FlowError("没有指定文字模型。请在 flowkit.env 里填写 FLOW_TEXT_MODEL（需支持看图）。")
        content = []
        for kind, val in parts:
            if kind == "text":
                if val:
                    content.append({"type": "text", "text": val})
            else:
                content.append({"type": "image_url", "image_url": {"url": data_url(val)}})
        body = {"model": self.text_model, "messages": [{"role": "user", "content": content}]}
        res = self._post("/chat/completions", json.dumps(body).encode("utf-8"), "application/json")
        try:
            msg = res["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise FlowError(f"文字模型返回格式不认识：{json.dumps(res, ensure_ascii=False)[:500]}")
        if isinstance(msg, list):  # 少数服务把 content 返回成分段数组
            msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
        return msg

    def image(self, prompt, images, size):
        """无参考图走 /images/generations，有参考图走 /images/edits（image[] 按顺序）。"""
        if not images:
            body = {"model": self.image_model, "prompt": prompt, "n": 1}
            if size:
                body["size"] = size
            res = self._post("/images/generations", json.dumps(body).encode("utf-8"),
                             "application/json")
        else:
            fields = [("model", self.image_model), ("prompt", prompt), ("n", "1")]
            if size:
                fields.append(("size", size))
            body, ctype = multipart(fields, [("image[]", p) for p in images])
            res = self._post("/images/edits", body, ctype)
        try:
            item = res["data"][0]
        except (KeyError, IndexError, TypeError):
            raise FlowError(f"生图模型返回格式不认识：{json.dumps(res, ensure_ascii=False)[:500]}")
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            with urllib.request.urlopen(item["url"], timeout=self.timeout) as r:
                return r.read()
        raise FlowError("生图模型没有返回图片（既没有 b64_json 也没有 url）。")


def data_url(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def multipart(fields, files):
    boundary = "----flowkit" + uuid.uuid4().hex
    out = bytearray()
    for name, val in fields:
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode()
        out += str(val).encode("utf-8") + b"\r\n"
    for name, path in files:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        fname = os.path.basename(path)
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{fname}\"\r\nContent-Type: {mime}\r\n\r\n").encode("utf-8")
        with open(path, "rb") as f:
            out += f.read()
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary

# ---------------------------------------------------------------- 槽位

SLOT_RE = re.compile(r"\{\{\s*(img\??:)?\s*([^{}]+?)\s*\}\}")
EXPR_RE = re.compile(r"^([A-Za-z_]\w*)((?:\[\d+\]|\.\w+)*)$")
ACCESSOR_RE = re.compile(r"\[(\d+)\]|\.(\w+)")


class Pending:
    """试跑前预览时，还没有产生的变量。"""

    def __init__(self, var):
        self.var = var


def find_slots(template):
    """返回 [(kind, expr)]，kind 为 text / img / img?。"""
    out = []
    for m in SLOT_RE.finditer(template):
        kind = {None: "text", "img:": "img", "img?:": "img?"}[m.group(1)]
        out.append((kind, m.group(2)))
    return out


def slot_root(expr):
    m = EXPR_RE.match(expr)
    return m.group(1) if m else expr


def eval_expr(expr, vars, ctx):
    m = EXPR_RE.match(expr)
    if not m:
        raise FlowError(f"槽位写法不认识：{{{{{expr}}}}}")
    name, rest = m.group(1), m.group(2)
    if name in ("prev", "anchor"):
        val = ctx.get(name)
    elif name in LOOP_CTX:
        if name not in ctx:
            raise FlowError(f"{{{{{name}}}}} 只能用在循环步骤里")
        val = ctx[name]
    elif name in vars:
        val = vars[name]
    else:
        return Pending(name)
    for idx, attr in ACCESSOR_RE.findall(rest):
        if isinstance(val, Pending) or val is None:
            return val
        if idx:
            i = int(idx)
            if not isinstance(val, list) or not (1 <= i <= len(val)):
                raise FlowError(f"{{{{{expr}}}}}：第 {i} 项不存在（当前共 "
                                f"{len(val) if isinstance(val, list) else 0} 项）")
            val = val[i - 1]
        elif attr == "all":
            val = to_text(val)
        elif attr == "inline":
            val = "、".join(to_text(x) for x in val) if isinstance(val, list) else to_text(val)
        elif attr == "count":
            val = len(val) if isinstance(val, list) else (0 if val in (None, "") else 1)
        elif attr == "current":
            r = ctx.get("round")
            if r is None:
                raise FlowError(f"{{{{{expr}}}}}：.current 只能用在循环步骤里")
            if not isinstance(val, list) or r >= len(val):
                raise FlowError(f"{{{{{expr}}}}}：对齐引用的列表长度不够（需要第 {r + 1} 项）")
            val = val[r]
        else:
            if not isinstance(val, dict):
                raise FlowError(f"{{{{{expr}}}}}：{attr} 前面的值不是 JSON 对象")
            val = val.get(attr)
    return val


def to_text(v):
    """槽位填入规则：列表按换行拼接，JSON 对象转成 JSON 文本，空值填空字符串。"""
    if v is None:
        return ""
    if is_img(v):
        raise FlowError("图片变量不能放进文字槽位，请改用 {{img:变量}}")
    if isinstance(v, list):
        return "\n".join(to_text(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def fill_prompt(template, vars, ctx):
    """把模板填成一次真实调用。

    返回 dict：
      text     发送的提示词全文（图片槽位替换为【图N】）
      parts    [("text", s) | ("image", path)] 按槽位顺序
      images   本次调用的图片路径列表
      pending  尚未产生的变量名（预览时出现）
    """
    kept = []
    for line in template.split("\n"):
        drop = False
        for kind, expr in find_slots(line):
            if kind == "text":
                continue
            v = eval_expr(expr, vars, ctx)
            if v is None and (kind == "img?" or slot_root(expr) in ("prev", "anchor")):
                drop = True     # 可选图片为空 / 锚点轮 / 链式第 1 轮：整行删除
        if not drop:
            kept.append(line)
    tpl = "\n".join(kept)

    parts, images, pending, text_out = [], [], [], []
    pos = 0
    for m in SLOT_RE.finditer(tpl):
        seg = tpl[pos:m.start()]
        pos = m.end()
        kind = {None: "text", "img:": "img", "img?:": "img?"}[m.group(1)]
        expr = m.group(2)
        v = eval_expr(expr, vars, ctx)
        if kind == "text":
            if isinstance(v, Pending):
                s = f"〔待生成：{expr}〕"
                pending.append(v.var)
            else:
                s = to_text(v)
            text_out.append(seg + s)
            parts.append(("text", seg + s))
            continue
        text_out.append(seg)
        parts.append(("text", seg))
        if isinstance(v, Pending):
            images.append(None)
            pending.append(v.var)
            marker = f"【图{len(images)}】"
        elif v is None:
            raise FlowError(f"必选图片 {{{{img:{expr}}}}} 没有值。若允许为空，请改用 {{{{img?:{expr}}}}} 或加 when 条件")
        elif isinstance(v, list):
            raise FlowError(f"{{{{img:{expr}}}}} 是一组图片，请写成 {{{{img:{expr}[i]}}}}")
        elif not is_img(v):
            raise FlowError(f"{{{{img:{expr}}}}} 引用的不是图片")
        else:
            images.append(v["__img__"])
            marker = f"【图{len(images)}】"
            parts.append(("image", v["__img__"]))
        text_out.append(marker)
        parts.append(("text", marker))
    text_out.append(tpl[pos:])
    parts.append(("text", tpl[pos:]))
    merged = []
    for kind, val in parts:        # 合并相邻文字段
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + val)
        else:
            merged.append((kind, val))
    return {"text": "".join(text_out), "parts": merged, "images": images,
            "pending": sorted(set(pending))}


# ---------------------------------------------------------------- 条件与数量

def is_empty(v):
    return v is None or v == "" or v == [] or v == {}


def eval_when(expr, vars):
    """返回 True / False，或 Pending（预览时变量还没产生）。"""
    if not expr:
        return True
    ors = re.split(r"\s+or\s+", expr.strip())
    any_pending = False
    for clause in ors:
        ok = True
        for atom in re.split(r"\s+and\s+", clause):
            r = eval_atom(atom.strip(), vars)
            if isinstance(r, Pending):
                any_pending = True
                ok = False
                break
            if not r:
                ok = False
                break
        if ok:
            return True
    return Pending("when") if any_pending else False


def eval_atom(atom, vars):
    neg = False
    if atom.startswith("not "):
        neg, atom = True, atom[4:].strip()
    m = re.fullmatch(r"has\(\s*([\w.\[\]]+)\s*\)", atom)
    if m:
        v = eval_expr(m.group(1), vars, {})
        if isinstance(v, Pending):
            return v
        r = not is_empty(v)
        return (not r) if neg else r
    m = re.fullmatch(r"count\(\s*([\w.\[\]]+)\s*\)\s*(<=|>=|=|<|>)\s*(\d+)", atom)
    if m:
        v = eval_expr(m.group(1), vars, {})
        if isinstance(v, Pending):
            return v
        n = len(v) if isinstance(v, list) else (0 if is_empty(v) else 1)
        k = int(m.group(3))
        r = {"<": n < k, "<=": n <= k, "=": n == k, ">=": n >= k, ">": n > k}[m.group(2)]
        return (not r) if neg else r
    raise FlowError(f"when 条件写法不认识：{atom}")


def eval_count_expr(expr, vars):
    """count 可以写成 "=16-count(lines)"，运行时算出确切数量。"""
    body = expr.lstrip("=")

    def rep(m):
        v = vars.get(m.group(1))
        return str(len(v) if isinstance(v, list) else 0)

    arith = re.sub(r"count\(\s*(\w+)\s*\)", rep, body)
    if not re.fullmatch(r"[\d\s+\-*/()]+", arith):
        raise FlowError(f"数量表达式写法不认识：{expr}")
    n = int(eval(arith, {"__builtins__": {}}, {}))
    return [n, n]


def count_bounds(out_spec, vars):
    c = out_spec.get("count")
    if c is None:
        return None
    if isinstance(c, str):
        return eval_count_expr(c, vars)
    if isinstance(c, int):
        return [c, c]
    return list(c)


# ---------------------------------------------------------------- 输出解析

class ParseError(Exception):
    pass


def extract_block(text, open_ch, close_ch):
    text = strip_fences(text)
    i, j = text.find(open_ch), text.rfind(close_ch)
    if i < 0 or j < i:
        raise ParseError(f"输出里找不到 {open_ch}…{close_ch}")
    try:
        return json.loads(text[i:j + 1])
    except json.JSONDecodeError as e:
        raise ParseError(f"JSON 解析失败：{e}")


def check_fields(obj, fields, where="JSON"):
    if not isinstance(obj, dict):
        raise ParseError(f"{where} 不是对象")
    for f in fields or []:
        cur = obj
        for part in f.split("."):
            if not isinstance(cur, dict) or part not in cur or is_empty(cur[part]):
                raise ParseError(f"{where} 缺少字段 {f}")
            cur = cur[part]


def check_count(n, bounds, what):
    if bounds and not (bounds[0] <= n <= bounds[1]):
        raise ParseError(f"{what}数量为 {n}，要求 {fmt_range(bounds)}")


def parse_output(raw, spec, vars):
    t = spec.get("type", "text")
    if t == "text":
        return strip_fences(raw).strip()
    if t == "list":
        body = strip_fences(raw)
        items = None
        if body.lstrip().startswith("["):
            try:
                arr = json.loads(body)
                if isinstance(arr, list) and all(isinstance(x, (str, int, float)) for x in arr):
                    items = [str(x).strip() for x in arr if str(x).strip()]
            except json.JSONDecodeError:
                pass
        if items is None:
            items = split_lines(body)
            if spec.get("strip_numbering", True):
                items = [NUMBERING_RE.sub("", x).strip() for x in items]
                items = [x for x in items if x]
        check_count(len(items), count_bounds(spec, vars), "列表")
        return items
    if t == "json":
        obj = extract_block(raw, "{", "}")
        check_fields(obj, spec.get("fields"))
        return obj
    if t == "json_list":
        arr = extract_block(raw, "[", "]")
        if not isinstance(arr, list):
            raise ParseError("输出不是 JSON 数组")
        for i, o in enumerate(arr, 1):
            check_fields(o, spec.get("fields"), f"第 {i} 项")
        check_count(len(arr), count_bounds(spec, vars), "JSON 数组")
        return arr
    raise FlowError(f"输出类型不认识：{t}")

# ---------------------------------------------------------------- 静态分析（validate）

def rng_div(r, k):
    return None if r is None else [r[0] // k, r[1] // k]


def rng_add(a, b):
    return None if a is None or b is None else [a[0] + b[0], a[1] + b[1]]


def analyze(flow):
    """建立变量目录并推算每一步的轮数。结果供 validate / render / plan 共用。"""
    cat = {}   # 变量 → {kind, type, size, step, verbatim, optional, name}
    for t in flow.get("inputs", {}).get("texts", []):
        cat[t["id"]] = {"kind": "input", "type": "list", "size": list(t["rows"]),
                        "verbatim": t.get("verbatim", False), "name": t.get("name", t["id"]),
                        "optional": not t.get("required", True), "step": None}
    im = flow.get("inputs", {}).get("image", {})
    if im.get("enabled"):
        cat["user_image"] = {"kind": "user_image", "type": "image", "size": None,
                             "name": im.get("name", "用户图片"),
                             "optional": not im.get("required", False), "step": None}
    for p in flow.get("presets", []):
        cat[p["id"]] = {"kind": "preset", "type": "image", "size": None,
                        "name": p.get("purpose", p["id"]), "optional": False, "step": None}
    rounds = {}
    for st in flow.get("steps", []):
        mode, k = parse_consume(st.get("consume"))
        over = st.get("over")
        osize = cat.get(over, {}).get("size") if over else None
        if mode == "per_item":
            r = osize
        elif mode == "grouped":
            r = rng_div(osize, k)
        else:
            r = [1, 1]
        rounds[st["id"]] = r
        out = st.get("output", {})
        oid = out.get("id")
        if not oid:
            continue
        loop = mode in ("per_item", "grouped")
        if st["type"] == "image":
            typ, size = ("images", r) if loop else ("image", None)
        elif st["type"] == "code":
            op = st.get("op", {})
            name = op.get("name")
            if name in ("compose", "overlay_text"):
                typ, size = "image", None
            else:
                typ = out.get("type", "list")
                c = out.get("count")
                if isinstance(c, list):
                    size = c
                elif isinstance(c, int):
                    size = [c, c]
                elif name == "merge":
                    size = [0, 0]
                    for l in op.get("lists", []):
                        s = cat.get(l, {}).get("size")
                        if cat.get(l, {}).get("step") and cat[l]["step"].get("when"):
                            s = [0, s[1]] if s else None
                        size = rng_add(size, s)
                else:
                    size = None
        else:
            if loop:
                typ, size = out.get("type", "text") + "[]", r
            else:
                typ = out.get("type", "text")
                c = out.get("count")
                size = c if isinstance(c, list) else ([c, c] if isinstance(c, int) else None)
        prev = cat.get(oid)
        cat[oid] = {"kind": "step", "type": typ, "size": size, "step": st,
                    "verbatim": out.get("verbatim", False), "name": out.get("name", oid),
                    "optional": bool(st.get("when")) and not (prev and prev["kind"] == "step"),
                    "branches": (prev.get("branches", [prev["step"]]) + [st]) if prev and prev["kind"] == "step" else [st]}
    return cat, rounds


def estimate(flow, cat, rounds):
    """出图数、文字调用数、生图调用数的最小～最大值。"""
    def step_range(st):
        r = rounds.get(st["id"])
        if r is None:
            return None
        return [0 if st.get("when") else r[0], r[1]]

    img = [0, 0]
    seen = {}
    txt_calls, img_calls = [0, 0], [0, 0]
    unknown = False
    for st in flow.get("steps", []):
        r = step_range(st)
        if r is None:
            unknown = True
            continue
        if st["type"] == "text":
            txt_calls = rng_add(txt_calls, r)
        if st["type"] == "image":
            img_calls = rng_add(img_calls, r)
        produces = st["type"] == "image" or (st["type"] == "code" and
                                             st.get("op", {}).get("name") in ("compose", "overlay_text"))
        if produces:
            oid = st.get("output", {}).get("id")
            seen.setdefault(oid, []).append(r)
    for oid, rs in seen.items():   # 同一输出 ID 的互斥分支只算一次
        img = rng_add(img, [min(x[0] for x in rs), max(x[1] for x in rs)])
    # 被 overlay_text 覆盖的原图不再单独计入交付：以 outputs 为准
    delivered = {o["var"].split(".")[0] for o in flow.get("outputs", [])}
    if delivered:
        img = [0, 0]
        for oid, rs in seen.items():
            if oid in delivered:
                img = rng_add(img, [min(x[0] for x in rs), max(x[1] for x in rs)])
        for v in delivered:
            if cat.get(v, {}).get("kind") in ("user_image", "preset"):
                img = rng_add(img, [1, 1])
    return {"images": None if unknown else img, "text_calls": None if unknown else txt_calls,
            "image_calls": None if unknown else img_calls}


def validate(flow):
    """返回 (问题列表, 分析结果)。问题为 (级别, 编号, 说明)，级别为 错误 / 提醒。"""
    issues = []

    def err(code, msg):
        issues.append(("错误", code, msg))

    def warn(code, msg):
        issues.append(("提醒", code, msg))

    try:
        cat, rounds = analyze(flow)
    except FlowError as e:
        return [("错误", "格式", str(e))], None

    texts = flow.get("inputs", {}).get("texts", [])
    im = flow.get("inputs", {}).get("image", {})
    # D11
    if not any(t.get("required", True) for t in texts) and not (im.get("enabled") and im.get("required")):
        err("D11", "至少要有 1 个必填输入（文字或图片）。")
    for t in texts:
        lo, hi = t["rows"]
        if lo < 0 or hi < lo:
            err("格式", f"输入 {t['id']} 的行数范围 {lo}～{hi} 不合理。")
        # default：建网页时预填进输入框的内容，不参与运行，只做格式检查。
        d = t.get("default")
        if d is not None:
            if not isinstance(d, str):
                err("格式", f"输入 {t['id']} 的 default 要是字符串（多行用 \\n 分隔）。")
            else:
                n = len(split_lines(d))
                if n and not (lo <= n <= hi):
                    err("格式", f"输入 {t['id']} 的 default 有 {n} 行，"
                                f"超出它自己的行数范围 {fmt_range(t['rows'])}。")
                mc = t.get("max_chars")
                if mc:
                    for i, ln in enumerate(split_lines(d), 1):
                        if len(ln) > mc:
                            err("格式", f"输入 {t['id']} 的 default 第 {i} 行有 {len(ln)} 个字，"
                                        f"超过单行上限 {mc} 字。")
    # D5 / G3
    presets = flow.get("presets", [])
    if len(presets) > MAX_PRESETS:
        err("D5", f"预置图有 {len(presets)} 张，最多 {MAX_PRESETS} 张。")
    for p in presets:
        if not re.fullmatch(r"preset_[1-3]", p["id"]):
            err("D5", f"预置图 ID 必须是 preset_1～preset_3，现在是 {p['id']}。")
        if not os.path.exists(os.path.join(flow["_dir"], p.get("file", ""))):
            err("D5", f"预置图 {p['id']} 的文件不存在：{p.get('file')}")

    defined = {t["id"] for t in texts} | {p["id"] for p in presets}
    if im.get("enabled"):
        defined.add("user_image")
    outputs_seen = {}
    for idx, st in enumerate(flow.get("steps", []), 1):
        sid = st.get("id", f"#{idx}")
        where = f"步骤 {idx}「{st.get('name', sid)}」"
        typ = st.get("type")
        if typ not in TYPE_NAMES:
            err("格式", f"{where}：type 必须是 text / image / code。")
            continue
        try:
            mode, k = parse_consume(st.get("consume"))
            ref, anchor_n = parse_ref_mode(st.get("ref_mode"))
        except FlowError as e:
            err("格式", f"{where}：{e}")
            continue
        loop = mode in ("per_item", "grouped")
        over = st.get("over")
        if mode != "once" and not over:
            err("格式", f"{where}：consume={mode} 时必须写 over（被消费的列表）。")
        if over and over not in defined:
            err("D3", f"{where}：over 引用的 {over} 在此之前没有产生。")
        if st.get("when"):
            for v in re.findall(r"(?:has|count)\(\s*(\w+)", st["when"]):
                if v not in defined:
                    err("D3", f"{where}：when 条件引用的 {v} 在此之前没有产生。")
        # D8 整除
        if mode == "grouped" and over in cat:
            info = cat[over]
            if info["kind"] == "input":
                t = next(x for x in texts if x["id"] == over)
                lo, hi = t["rows"]
                if t.get("multiple_of") != k or lo % k or hi % k or lo < k:
                    err("D8", f"{where}：按每 {k} 项一组消费 {over}，但输入的行数范围 {lo}～{hi} 不能保证整除。"
                              f"请在输入上设 multiple_of={k}，并把行数上下限改成 {k} 的倍数；或加一个补全步骤。")
            else:
                ps = info.get("step") or {}
                if (ps.get("output") or {}).get("multiple_of") != k:
                    err("D8", f"{where}：按每 {k} 项一组消费 {over}，请在产生 {over} 的步骤输出上写 "
                              f"multiple_of={k}，并在提示词里要求数量是 {k} 的倍数。")
        if ref and not loop:
            warn("格式", f"{where}：ref_mode 只对循环步骤有意义，这里会被忽略。")
        if ref == "anchor":
            r = rounds.get(sid)
            if r and anchor_n > r[0]:
                err("D12", f"{where}：anchor({anchor_n}) 需要至少 {anchor_n} 轮，但最少可能只有 {r[0]} 轮。")
        # 提示词与槽位
        if typ in ("text", "image"):
            prompt = st.get("_prompt", "")
            if not prompt.strip():
                err("格式", f"{where}：缺少提示词（prompt 或 prompt_file）。")
            slots = find_slots(prompt)
            n_img = sum(1 for kind, _ in slots if kind != "text")
            if n_img > MAX_IMAGES_PER_CALL:
                err("D2", f"{where}：一次调用用了 {n_img} 张图，最多 {MAX_IMAGES_PER_CALL} 张。")
            for kind, expr in slots:
                root = slot_root(expr)
                if root in ("item",) and mode != "per_item":
                    err("D3", f"{where}：{{{{{expr}}}}} 只能用在 per_item 循环里。")
                elif root == "group" and mode != "grouped":
                    err("D3", f"{where}：{{{{{expr}}}}} 只能用在 grouped 循环里。")
                elif root in ("index", "total") and not loop:
                    err("D3", f"{where}：{{{{{expr}}}}} 只能用在循环步骤里。")
                elif root == "prev" and ref != "chain":
                    err("D3", f"{where}：{{{{img:prev}}}} 需要 ref_mode=chain。")
                elif root == "anchor" and ref != "anchor":
                    err("D3", f"{where}：{{{{img:anchor}}}} 需要 ref_mode=anchor(n)。")
                elif root not in LOOP_CTX | {"prev", "anchor"} and root not in defined:
                    err("D3", f"{where}：槽位 {{{{{expr}}}}} 引用的 {root} 在此之前没有产生（检查拼写或步骤顺序）。")
                if kind == "text" and root in cat and cat[root]["type"] in ("image", "images"):
                    err("D3", f"{where}：{root} 是图片，要写成 {{{{img:{root}}}}}。")
                if kind != "text" and root in cat and cat[root]["type"] not in ("image", "images"):
                    err("D3", f"{where}：{root} 不是图片，不能放进图片槽位。")
                if kind == "img" and root in cat and cat[root].get("optional"):
                    cond = st.get("when") or ""
                    if f"has({root})" not in cond.replace(" ", ""):
                        err("D4", f"{where}：{root} 可能为空，请改用 {{{{img?:{root}}}}}，或给本步加 when: has({root})。")
                if ".current" in expr:
                    info = cat.get(root, {})
                    ps = info.get("step") or {}
                    same = ps.get("over") == over and parse_consume(ps.get("consume"))[0] == mode
                    if not same:
                        warn("D9", f"{where}：{{{{{expr}}}}} 要求 {root} 与 {over} 一样长，脚本会在运行时检查。")
            if typ == "image" and any(cat.get(slot_root(e), {}).get("verbatim") or
                                      (slot_root(e) in ("item", "group") and cat.get(over, {}).get("verbatim"))
                                      for kd, e in slots if kd == "text"):
                if "逐字" not in prompt:
                    warn("D10", f"{where}：用到了需要逐字保留的文字，提示词里应写明“逐字呈现，不改写、不漏字、不多字”。")
        # 输出
        out = st.get("output") or {}
        oid = out.get("id")
        if not oid:
            err("格式", f"{where}：缺少 output.id。")
        if typ == "text":
            ot = out.get("type")
            if ot not in ("text", "list", "json", "json_list"):
                err("D6", f"{where}：output.type 必须是 text / list / json / json_list。")
            if ot in ("list", "json_list") and out.get("count") is None:
                err("D6", f"{where}：列表输出要写 count（数量范围）。")
            if ot in ("json", "json_list") and not out.get("fields"):
                err("D6", f"{where}：JSON 输出要写 fields（必须有的字段）。")
        if typ == "image":
            if not st.get("image_size"):
                err("D7", f"{where}：生图步骤要写 image_size，如 1024x1536。")
            else:
                sz = str(st.get("image_size"))
                if sz not in IMAGE_SIZES:
                    warn("D7", f"{where}：image_size「{sz}」不在常用尺寸里"
                               f"（{' / '.join(sorted(IMAGE_SIZES))}）。"
                               "这个值会原样发给接口，写错会被接口拒绝；"
                               "确实要用别的尺寸时忽略本提醒。")
            if not st.get("role"):
                err("D7", f"{where}：生图步骤要写 role（页面角色）。")
            elif st.get("role") not in ROLE_NAMES:
                warn("D7", f"{where}：role「{st.get('role')}」不在标准角色表里"
                           f"（{' / '.join(ROLE_NAMES)}）。自定义角色需要能说出显示名，"
                           "改回表内角色可避免下游网页认不出。")
        if typ == "code":
            opn = (st.get("op") or {}).get("name")
            if opn not in CODE_OPS:
                err("格式", f"{where}：op.name 必须是 {' / '.join(CODE_OPS)}。")
            if opn in ("compose", "overlay_text") and not st.get("role"):
                err("D7", f"{where}：{opn} 产出图片，要写 role。")
            for v in code_reads(st):
                if v not in defined:
                    err("D3", f"{where}：op 引用的 {v} 在此之前没有产生。")
        if oid:
            if oid in outputs_seen and not (st.get("when") and outputs_seen[oid].get("when")):
                err("格式", f"{where}：输出 {oid} 与前面的步骤重名。只有条件互斥的分支才能共用输出 ID。")
            outputs_seen[oid] = st
            defined.add(oid)
    # D10 逐字文字要直接进图
    for t in texts:
        if t.get("verbatim"):
            used = False
            for st in flow.get("steps", []):
                if st.get("type") != "image":
                    continue
                for kd, e in find_slots(st.get("_prompt", "")):
                    r = slot_root(e)
                    if r == t["id"] or (r in ("item", "group") and st.get("over") == t["id"]):
                        used = True
                if st.get("over") and cat.get(st["over"], {}).get("kind") == "step":
                    ps = cat[st["over"]]["step"]
                    if ps.get("type") == "code" and t["id"] in code_reads(ps):
                        used = True
            if not used:
                warn("D10", f"输入 {t['id']} 标了逐字保留，但没有被任何生图步骤直接引用；确认没有被文字模型改写后再进图。")
    for o in flow.get("outputs", []):
        if o["var"].split(".")[0] not in defined:
            err("D3", f"输出交付里的 {o['var']} 没有被任何步骤产生。")
    check_remake(flow, cat, warn)
    est = estimate(flow, cat, rounds)
    if est["images"] is None:
        warn("D1", "无法推算出图数量：有列表没有写明数量范围。")
    else:
        lo, hi = est["images"]
        if hi > MAX_OUTPUT_IMAGES:
            err("D1", f"最多可能出 {hi} 张图，超过上限 {MAX_OUTPUT_IMAGES} 张。请收紧输入行数或列表数量。")
        if hi < 1:
            err("D1", "这个流程不会产出任何图片。")
        elif lo < 1:
            warn("D1", "某些情况下可能 0 张图（条件步骤全部跳过），请确认。")
    return issues, {"cat": cat, "rounds": rounds, "est": est}


SUBJECT_TYPES = ["cartoon", "human", "animal", "plant", "object"]
SUBJECT_TYPE_NAMES = {"cartoon": "卡通角色", "human": "真人", "animal": "动物", "plant": "植物", "object": "产品/物件"}

# D17 机械扫描用：保留清单里出现这些具体名词，或提示词正文里出现这些身份词，都是写死了。
# 只列常见高置信词，避免误报（如“配色”“服装”这类类别词不在表内）。
SPECIFIC_NOUNS = [
    "耳朵", "尾巴", "双马尾", "马尾", "发卡", "发带", "护目镜", "眼镜", "围巾", "背包", "书包",
    "帽子", "披风", "斗篷", "裙子", "领带", "项圈", "铃铛", "翅膀", "角", "斑点", "条纹",
]
IDENTITY_WORDS = [
    "狐狸", "猫", "小猫", "狗", "小狗", "兔子", "熊", "熊猫", "老虎", "狮子", "企鹅", "鸭",
    "青蛙", "老鼠", "猴子", "大象", "长颈鹿", "恐龙", "机器人", "少女", "女孩", "男孩",
]
# 类别词白名单：保留清单项里包含这些词，视为合法类别，不再扫描其中的具体名词。
KEEP_CATEGORY_WORDS = [
    "脸型", "五官", "发型", "发饰", "配色", "颜色", "服装", "衣着", "标志物", "身体比例", "比例",
    "物种", "品种", "毛色", "花纹", "体型", "形态", "标志部位", "配饰", "叶形", "花形", "容器",
    "外形", "轮廓", "材质", "标签", "位置", "面部", "肤色", "神态", "气质",
]


def scan_hardcoded_subject(keep, pr):
    """D17 机械扫描：保留清单里的具体名词 + 提示词正文里的身份词。

    返回 (问题列表, 命中的具体名词)。
    """
    hits = []
    for item in keep:
        it = str(item)
        # 括号里的内容往往是具体描述，如“标志部位（耳朵、尾巴）”
        inner = re.sub(r"^[^（(]*[（(]|）|\)$", "", it) if ("（" in it or "(" in it) else ""
        probe = inner if inner else it
        if inner and any(w in it for w in KEEP_CATEGORY_WORDS):
            # 有类别词打底，但括号里塞了具体名词，仍算违规
            noun = [n for n in SPECIFIC_NOUNS if n in inner]
            if noun:
                hits.append(("preserve", it, noun))
        elif any(w in it for w in KEEP_CATEGORY_WORDS):
            continue
        else:
            noun = [n for n in SPECIFIC_NOUNS if n in it]
            if noun:
                hits.append(("preserve", it, noun))
    # 提示词正文里的身份词（预置图说明行除外：那里允许用类别名）
    word = [w for w in IDENTITY_WORDS if w in pr]
    return hits, word

DESIGN_LAYERS = [("style", "风格层（固定）"), ("relation", "关系层（固定）"),
                 ("varies", "变化层（槽位）"), ("avoid", "不照搬")]


def check_remake(flow, cat, warn):
    """爆款复刻相关的提醒 D13～D17：参考案例、提示词设计、迁移测试、固定主角保留说明。"""
    ref = flow.get("reference")
    if not ref:
        warn("D13", "没有参考案例（reference）。复刻要从一篇具体的笔记开始：原图按顺序，加上标题、正文、标签。")
    else:
        imgs = ref.get("images") or []
        if not imgs:
            warn("D13", "参考案例没有原图（reference.images）。没有原图就无法做还原对比。")
        for i, ri in enumerate(imgs, 1):
            f = ri.get("file")
            if not f:
                warn("D13", f"参考图 {i} 只有文字说明，没有图片文件。")
            elif not os.path.exists(os.path.join(flow["_dir"], f)):
                warn("D13", f"参考图 {i} 的文件不存在：{f}")
        miss = [n for k, n in (("title", "标题"), ("body", "正文"), ("tags", "标签")) if not ref.get(k)]
        if miss:
            warn("D13", "参考案例缺少：" + "、".join(miss) + "。")
    presets = {p["id"] for p in flow.get("presets", [])}
    for idx, st in enumerate(flow.get("steps", []), 1):
        if st.get("type") != "image":
            continue
        where = f"步骤 {idx}「{st.get('name', st.get('id'))}」"
        d = st.get("design") or {}
        miss = [n for k, n in DESIGN_LAYERS[:3] if not d.get(k)]
        if miss:
            warn("D14", f"{where}：提示词设计说明（design）缺少 " + "、".join(miss) +
                 "。写清楚这段提示词固定复刻什么、什么跟着输入变。")
        roots = {slot_root(e) for _, e in find_slots(st.get("_prompt", ""))}
        if not (roots - presets):
            warn("D15", f"{where}：提示词里没有任何随输入变化的槽位，每次都会画出差不多的图。"
                        "检查是不是把原图的具体画面写死了。")
    subj = flow.get("subject") or {}
    if subj.get("fixed"):
        keep = subj.get("preserve") or []
        if subj.get("type") and subj["type"] not in SUBJECT_TYPES:
            warn("D17", f"subject.type 只能是 {' / '.join(SUBJECT_TYPES)}，现在是 {subj['type']}。")
        if not keep:
            warn("D17", "固定主角没有写保留清单（subject.preserve）。按主角类型列特征类别，"
                        "如卡通角色：脸型、五官画法、发型/发饰、配色、服装、标志物、身体比例。")
        else:
            hits, _ = scan_hardcoded_subject(keep, "")
            for _, item, noun in hits:
                warn("D17", f"保留清单「{item}」里写了具体名词（{'、'.join(noun)}）。"
                            "只写特征类别，主角长什么样由预置图决定，不由文字决定。")
                    # 保留清单整体扫一遍身份词
            w = [x for x in IDENTITY_WORDS if any(x in str(i) for i in keep)]
            if w:
                warn("D17", "保留清单里出现了身份词（" + "、".join(w) + "）。"
                            "改成特征类别（物种、毛色和花纹、体型……）。")
        if not presets:
            warn("D17", "固定主角但没有预置图。请把主角图设为预置图，每次生图都传进去。")
        for idx, st in enumerate(flow.get("steps", []), 1):
            if st.get("type") != "image":
                continue
            pr = st.get("_prompt", "")
            if not ({slot_root(e) for _, e in find_slots(pr)} & presets):
                continue
            where = f"步骤 {idx}「{st.get('name', st.get('id'))}」"
            if "保持" not in pr:
                warn("D17", f"{where}：有固定主角，但提示词里没有“保持【图N】的……”保留说明，主角容易走样。")
            else:
                lost = [k for k in keep if k not in pr]
                if lost:
                    warn("D17", f"{where}：保留说明里没写到 " + "、".join(lost) + "。")
            if "不要照搬" not in pr:
                warn("D17", f"{where}：缺“表情、动作……按本次内容重新设计，不要照搬【图N】”，"
                            "主角容易每张都摆同一个姿势。")
            # 机械扫描：提示词正文里写死了物种名或身份词
            _, iw = scan_hardcoded_subject([], pr)
            if iw:
                warn("D17", f"{where}：提示词里写死了身份词（{'、'.join(iw)}）。"
                            "主角是什么由预置图决定，正文里要写成“固定主角（{类型}）”这样的角色位。")
    else:
        # 没有固定主角时，也不该在提示词里钉死具体身份
        for idx, st in enumerate(flow.get("steps", []), 1):
            if st.get("type") != "image":
                continue
            pr = st.get("_prompt", "")
            if "@" in pr or "preset" in pr:
                continue
            _, iw = scan_hardcoded_subject([], pr)
            if iw and ("主角" in pr or "角色" in pr):
                warn("D17", f"步骤 {idx}「{st.get('name', st.get('id'))}」：没有固定主角，"
                            f"但提示词里写死了身份词（{'、'.join(iw)}）。改成通用角色位（“主角”），"
                            "具体画什么由本次内容决定。")

    if not flow.get("transfer_input"):
        warn("D16", "没有迁移测试输入（transfer_input）。请准备一份和原作内容完全不同的输入，"
                    "用来检查换了内容以后风格还在、画面会跟着变。")


CODE_OPS = ["merge", "pick", "slice", "split_lines", "compose", "overlay_text"]


def code_reads(st):
    op = st.get("op") or {}
    n = op.get("name")
    if n == "merge":
        return list(op.get("lists", []))
    if n == "pick":
        return [slot_root(op.get("path", ""))]
    if n in ("slice", "split_lines"):
        return [op.get("list") or op.get("text")]
    if n == "compose":
        return [slot_root(x) for x in op.get("images", [])]
    if n == "overlay_text":
        return [slot_root(op.get("image", ""))] + [slot_root(e) for kd, e in find_slots(op.get("text", "")) if kd == "text"]
    return []


def step_reads(st):
    if st["type"] == "code":
        reads = code_reads(st)
    else:
        reads = [slot_root(e) for _, e in find_slots(st.get("_prompt", ""))]
    if st.get("over"):
        reads.append(st["over"])
    reads += re.findall(r"(?:has|count)\(\s*(\w+)", st.get("when") or "")
    out = []
    for r in reads:
        if r and r not in LOOP_CTX | {"prev", "anchor"} and r not in out:
            out.append(r)
    return out

# ---------------------------------------------------------------- 人读说明（render）

GENERAL_RULES = """\
以下规则对所有玩法相同。实现网页或脚本时必须照此执行，不能自行发挥。

**R-1 用户文字拆分**：按换行拆成列表，去掉每行首尾空格，忽略空行。空行不表示分组。

**R-2 文字槽位填入**
- `{{x}}`：文本原样填入；列表按换行拼接全部项；JSON 对象填入其 JSON 文本；空值填空字符串。
- `{{x[i]}}` 第 i 项（从 1 开始）；`{{x.all}}` 按换行拼接；`{{x.inline}}` 用“、”拼接；`{{x.count}}` 项数；`{{x.字段}}` 取 JSON 字段，可多级。
- 循环内：`{{item}}` 当前项（可接 `.字段`）；`{{group[j]}}` 当前组第 j 项；`{{index}}` 当前轮序号，两位数，从 01 开始；`{{total}}` 总轮数；`{{x.current}}` 列表 x 中与当前轮同序号的一项。

**R-3 图片槽位填入**
- 每次调用中，图片按槽位在提示词里出现的先后编号为 图1、图2……，槽位所在位置替换为文字 `【图N】`，图片本身按同样顺序传给模型。
- `{{img?:x}}`：x 有值就传；x 为空时，删除该槽位所在的整行。
- `ref_mode=anchor(n)` 时第 n 轮、`ref_mode=chain` 时第 1 轮：含 `{{img:anchor}}` / `{{img:prev}}` 的整行删除。
- 一次调用的图片总数不超过 5 张。

**R-4 模型调用方式（OpenAI 兼容接口）**
- 文字模型：`POST {Base URL}/chat/completions`，一条 user 消息；content 按提示词顺序由文字段和图片段（data URL）交替组成。
- 生图模型（默认 gpt-image-2）：没有图片时 `POST /images/generations`（JSON：model、prompt、size、n=1）；有图片时 `POST /images/edits`（multipart：model、prompt、size、n=1，以及按顺序排列的多个 `image[]`）。返回 `data[0].b64_json` 或 `data[0].url`。

**R-5 输出解析**
- text：去掉 ``` 代码围栏和首尾空白。
- list：去掉代码围栏；如果是 JSON 字符串数组就直接用；否则按行拆分、去空行、去掉行首编号（如 `1.` `01、` `-` `第3条：`）。
- json：取第一个 `{` 到最后一个 `}` 解析；json_list：取第一个 `[` 到最后一个 `]` 解析。
- 按步骤声明检查数量、字段、倍数。不通过视为输出不合规。

**R-6 失败处理**
- 模型输出不合规：自动重试，最多 2 次；仍失败则停在该步，展示原始输出，允许单步重试。
- 接口报错：停在该步，展示错误，允许单步重试。
- 用户输入不合规（行数、倍数、必填、字数、图片）：提交前拦截，指出具体哪一项，不自动截断。

**R-7 循环与执行顺序**
- 步骤按编号顺序执行；`when` 不满足的步骤跳过，其输出为空。
- `per_item` 每项一轮，`grouped(k)` 按位置每 k 项一轮（第 1～k 项为第 1 组），`whole_list` 与 `once` 只执行 1 次。
- 每轮结果按轮次顺序收集成列表，作为该步输出。
- 参考方式：无或 `fixed` 时各轮可并行；`anchor(n)` 先执行第 n 轮，其余并行；`chain` 逐轮串行。
- 条件互斥的两个步骤可以共用同一个输出变量，后续步骤不必关心走了哪个分支。

**R-8 人工确认**：标记“完成后暂停确认”的步骤执行后暂停，展示结果；用户可修改文字、重新生成本步，或确认继续。修改后的结果要重新通过 R-5 的检查。

**R-9 交付**：按“输出交付”表的顺序展示；图片文件名为 `序号_显示名.png`（列表图片再加两位序号），文字项提供一键复制。
"""


def var_source(var, cat):
    info = cat.get(var)
    if not info:
        return f"{var}（未定义）"
    if info["kind"] == "input":
        return f"用户输入「{info['name']}」"
    if info["kind"] == "user_image":
        return f"用户上传的图片「{info['name']}」"
    if info["kind"] == "preset":
        return f"预置图 {var}（{info['name']}）"
    names = "／".join(f"「{s['name']}」" for s in info.get("branches", [info["step"]]))
    return f"步骤{names}的输出"


def describe_slot(kind, expr, st, cat):
    m = EXPR_RE.match(expr)
    root = slot_root(expr)
    accs = ACCESSOR_RE.findall(m.group(2)) if m else []
    over = st.get("over")
    if root == "item":
        src = f"当前项：{over} 的第 {{index}} 项"
    elif root == "group":
        src = f"当前组：{over} 中本轮的一组"
    elif root == "index":
        src = "当前轮序号（01、02…）"
    elif root == "total":
        src = "本步总轮数"
    elif root == "prev":
        src = "上一轮生成的图"
    elif root == "anchor":
        src = "锚点轮生成的图"
    else:
        src = var_source(root, cat)
    how = []
    for idx, attr in accs:
        if idx:
            how.append(f"取第 {idx} 项")
        elif attr == "all":
            how.append("全部项按换行拼接")
        elif attr == "inline":
            how.append("全部项用“、”拼接")
        elif attr == "count":
            how.append("取项数")
        elif attr == "current":
            how.append("取与当前轮同序号的一项")
        else:
            how.append(f"取字段 {attr}")
    if kind == "text":
        t = cat.get(root, {}).get("type", "")
        if not how and t in ("list", "text[]", "json[]", "list[]", "json_list"):
            how.append("全部项按换行拼接")
        fill = "，".join(how) or "原样填入"
    else:
        fill = ("，".join(how) + "，" if how else "") + "作为参考图传给模型，此处写为【图N】"
        if kind == "img?":
            fill += "；为空时删除整行"
        if root == "prev":
            fill += "；第 1 轮删除整行"
        if root == "anchor":
            fill += "；锚点轮自身删除整行"
    return src, fill


def describe_output(st, cat):
    out = st.get("output", {})
    mode, k = parse_consume(st.get("consume"))
    t = out.get("type", "image" if st["type"] == "image" else "text")
    lines = []
    if st["type"] == "image":
        lines.append("保存生成的图片。")
    elif st["type"] == "code":
        lines.append(describe_op(st))
    elif t == "text":
        lines.append("去掉代码围栏和首尾空白，原样保存。")
    elif t == "list":
        lines.append("解析为列表：若是 JSON 字符串数组就直接用；否则按行拆分、去空行"
                     + ("、去掉行首编号" if out.get("strip_numbering", True) else "") + "。")
    elif t == "json":
        lines.append("取第一个 { 到最后一个 } 解析为 JSON 对象。")
    elif t == "json_list":
        lines.append("取第一个 [ 到最后一个 ] 解析为 JSON 数组。")
    c = out.get("count")
    if c is not None and st["type"] != "image":
        lines.append(f"数量要求：{c.lstrip('=') if isinstance(c, str) else fmt_range(c if isinstance(c, list) else [c, c])}。")
    if out.get("multiple_of"):
        lines.append(f"数量必须是 {out['multiple_of']} 的倍数。")
    if out.get("fields"):
        lines.append("必须包含字段：" + "、".join(f"`{f}`" for f in out["fields"]) + "。")
    if out.get("verbatim"):
        lines.append("结果标记为逐字保留：后续进图时不得改写。")
    if mode in ("per_item", "grouped"):
        lines.append(f"每轮结果按轮次顺序收集成列表 `{out.get('id')}`。")
    if st["type"] == "text":
        lines.append("不合规时自动重试，最多 2 次；仍失败则停止并保留原始输出。")
    return " ".join(lines)


def describe_op(st):
    op = st.get("op", {})
    n = op.get("name")
    if n == "merge":
        return "把 " + " + ".join(f"`{x}`" for x in op.get("lists", [])) + " 按顺序拼成一个列表，为空的跳过。"
    if n == "pick":
        return f"从 JSON 中取出 `{op.get('path')}`。"
    if n == "slice":
        return f"取 `{op.get('list')}` 的第 {op.get('start', 1)}～{op.get('end', '末')} 项。"
    if n == "split_lines":
        return f"把 `{op.get('text')}` 按行拆成列表（规则同 R-1）。"
    if n == "compose":
        return (f"把 {'、'.join(op.get('images', []))} 按版式 `{op.get('layout', 'row')}` 拼成一张图，"
                f"间距 {op.get('gap', 0)}px，背景 {op.get('background', '#FFFFFF')}"
                + (f"，画布 {op['size'][0]}x{op['size'][1]}" if op.get("size") else "") + "。")
    if n == "overlay_text":
        return (f"在 `{op.get('image')}` 上写入文字「{op.get('text')}」，位置 {op.get('position', 'bottom')}，"
                f"字号 {op.get('font_size', 48)}，字体文件 {op.get('font', '（未指定）')}。")
    return str(op)


def describe_consume(st, rounds):
    mode, k = parse_consume(st.get("consume"))
    r = fmt_range(rounds.get(st["id"]))
    over = st.get("over")
    return {"once": "执行 1 次",
            "whole_list": f"整组一次：`{over}` 全部项一起交给模型，执行 1 次",
            "per_item": f"逐项循环：`{over}` 每项 1 轮，共 {r} 轮",
            "grouped": f"分组循环：`{over}` 每 {k} 项 1 轮，共 {r} 轮"}[mode]


def describe_ref(st):
    ref, n = parse_ref_mode(st.get("ref_mode"))
    if not ref:
        return "—"
    return {"fixed": "统一参考同一张已生成的图（可并行）",
            "anchor": f"以第 {n} 轮为锚点，其余各轮参考它（第 {n} 轮先执行）",
            "chain": "每轮参考上一轮（逐张串行）"}[ref]


def mm(s):
    return s.replace('"', "#quot;")


def mermaid(flow, cat, rounds):
    L = ["flowchart TD"]
    node = {}
    for t in flow.get("inputs", {}).get("texts", []):
        node[t["id"]] = "in_" + t["id"]
        L.append(f'  in_{t["id"]}[/"输入：{mm(t.get("name", t["id"]))}<br/>{t["id"]} · {fmt_range(t["rows"])} 行"/]')
    if "user_image" in cat:
        node["user_image"] = "in_user_image"
        L.append(f'  in_user_image[/"上传图片：{mm(cat["user_image"]["name"])}"/]')
    for p in flow.get("presets", []):
        node[p["id"]] = "pre_" + p["id"]
        L.append(f'  pre_{p["id"]}[("预置图 {p["id"]}<br/>{mm(p.get("purpose", ""))}")]')
    for i, st in enumerate(flow.get("steps", []), 1):
        sid = "s_" + st["id"]
        extra = [TYPE_NAMES[st["type"]]]
        mode, k = parse_consume(st.get("consume"))
        if mode != "once":
            extra.append({"per_item": "逐项×", "grouped": f"每{k}项×", "whole_list": "整组×"}[mode]
                         + fmt_range(rounds.get(st["id"])))
        if st.get("when"):
            extra.append("条件：" + st["when"])
        if st.get("ref_mode"):
            extra.append("参考：" + st["ref_mode"])
        if st.get("confirm"):
            extra.append("⏸ 人工确认")
        label = f'{i}. {mm(st["name"])}<br/>{mm(" · ".join(extra))}'
        shape = {"text": f'{sid}("{label}")', "image": f'{sid}["{label}"]',
                 "code": f'{sid}{{{{"{label}"}}}}'}[st["type"]]
        L.append("  " + shape)
        L.append(f"  class {sid} {st['type']}")
        for v in step_reads(st):
            src = node.get(v)
            if isinstance(src, list):
                for s in src:
                    L.append(f"  {s} -->|{v}| {sid}")
            elif src:
                L.append(f"  {src} -->|{v}| {sid}")
        oid = st.get("output", {}).get("id")
        if oid:
            if st.get("when") and isinstance(node.get(oid), (str, list)) and str(node.get(oid)).startswith("s_") or isinstance(node.get(oid), list):
                prev = node[oid] if isinstance(node[oid], list) else [node[oid]]
                node[oid] = prev + [sid]
            else:
                node[oid] = sid
    outs = flow.get("outputs", [])
    if outs:
        names = "、".join(o.get("name", o["var"]) for o in outs)
        L.append(f'  out[["最终交付：{mm(names)}"]]')
        done = set()
        for o in outs:
            v = o["var"].split(".")[0]
            srcs = node.get(v)
            for s in (srcs if isinstance(srcs, list) else [srcs]):
                if s and (s, v) not in done:
                    done.add((s, v))
                    L.append(f"  {s} -->|{v}| out")
    L += ["  classDef text fill:#E8F1FF,stroke:#3B7DDD",
          "  classDef image fill:#FFF1E0,stroke:#E08A1E",
          "  classDef code fill:#F0F0F0,stroke:#888"]
    return "\n".join(L)


def collapse_slots(slots):
    """去重，并把 {{x[1]}}、{{x[2]}}…连续编号的槽位合并成一行。返回 [(kind, expr, last_expr|None)]。"""
    out, seen = [], set()
    for kind, expr in slots:
        if (kind, expr) in seen:
            continue
        seen.add((kind, expr))
        m = re.fullmatch(r"(\w+)\[(\d+)\]", expr)
        if m and out:
            pk, pe, pl = out[-1]
            pm = re.fullmatch(r"(\w+)\[(\d+)\]", pl or pe)
            if pk == kind and pm and pm.group(1) == m.group(1) and int(m.group(2)) == int(pm.group(2)) + 1:
                out[-1] = (pk, pe, expr)
                continue
        out.append((kind, expr, None))
    return out


def md_cell(v):
    if isinstance(v, list):
        v = "；".join(str(x) for x in v)
    return str(v).replace("|", "\\|").replace("\n", "<br>")


def tags_text(tags):
    if isinstance(tags, str):
        tags = tags.split()
    return " ".join(t if str(t).startswith("#") else "#" + str(t) for t in tags or [])


def render_reference(flow, w):
    ref = flow.get("reference")
    w("### 参考案例（复刻目标）\n")
    if not ref:
        w("未提供。\n")
        return
    if ref.get("note"):
        w(f"来源：{ref['note']}\n")
    imgs = ref.get("images") or []
    if imgs:
        w("| 顺序 | 原图 | 这张图是什么 |\n|---|---|---|")
        for i, ri in enumerate(imgs, 1):
            f = ri.get("file")
            pic = f"![原图{i}]({f})" if f else "（未附图片）"
            w(f"| {i} | {pic} | {md_cell(ri.get('note', ''))} |")
        w("")
    for k, n in (("title", "原标题"), ("body", "原正文")):
        if ref.get(k):
            w(f"**{n}**\n\n````text\n{ref[k]}\n````\n")
    if ref.get("tags"):
        w(f"**原标签**：{tags_text(ref['tags'])}\n")
    w("复刻验收用两份输入：`sample_input` 为**还原测试**（尽量用原作内容，结果应接近原作）；"
      "`transfer_input` 为**迁移测试**（换成完全不同的内容，风格应保持、画面应跟着内容变）。\n")


def render(flow, issues, an):
    cat, rounds, est = an["cat"], an["rounds"], an["est"]
    o = []
    w = o.append
    w(f"# 玩法：{flow['name']}（{flow['id']}）\n")
    w(f"> 流程类型：`{flow.get('flow_type', '—')}` · 规格定义 v{flow.get('spec_version', SPEC_VERSION)}  ")
    w("> 本文件由 flowkit 根据 flow.json 自动生成。要修改，请改 flow.json 后重新生成，不要直接改本文件。\n")
    w("## 1. 概述\n")
    w(flow.get("summary", "") + "\n")
    if flow.get("source_note"):
        w(f"参考来源：{flow['source_note']}\n")
    if est["images"]:
        w(f"- 出图数量：{fmt_range(est['images'])} 张")
        w(f"- 每次运行调用：文字模型 {fmt_range(est['text_calls'])} 次，生图模型 {fmt_range(est['image_calls'])} 次")
    models = flow.get("models", {})
    w(f"- 生图模型：{models.get('image', DEFAULT_IMAGE_MODEL)}；文字模型：{models.get('text', '不指定（须支持看图）')}\n")
    render_reference(flow, w)

    w("## 2. 流程图\n")
    w("蓝色圆角为文字模型调用，橙色方框为生图模型调用，灰色六边形为程序处理；连线上是传递的变量。\n")
    w("```mermaid\n" + mermaid(flow, cat, rounds) + "\n```\n")

    w("## 3. 输入\n")
    texts = flow.get("inputs", {}).get("texts", [])
    if texts:
        w("| 变量 | 显示名 | 行数 | 倍数 | 必填 | 单行字数上限 | 逐字保留 | 默认值 | 输入框提示 |")
        w("|---|---|---|---|---|---|---|---|---|")
        for t in texts:
            w(f"| `{t['id']}` | {t.get('name', '')} | {fmt_range(t['rows'])} | {t.get('multiple_of') or '—'} | "
              f"{'是' if t.get('required', True) else '否'} | {t.get('max_chars') or '—'} | "
              f"{'是' if t.get('verbatim') else '否'} | "
              f"{(t.get('default') or '—') if isinstance(t.get('default') or '', str) else '（见下）'} | "
              f"{t.get('placeholder', '').replace(chr(10), ' / ')} |")
        for t in texts:
            d = t.get("default")
            if d and not isinstance(d, str):
                w(f"\n`{t['id']}` 的默认值（建网页时预填进输入框）：\n\n```text\n{d}\n```")
        w("")
        w("> 「默认值」是**建网页时预填进输入框**的内容，方便用户少打字；它不会自动进入运行，"
          "运行输入仍然来自 `sample_input`、`--input` 或用户在网页上填的内容。\n")
    im = flow.get("inputs", {}).get("image", {})
    if im.get("enabled"):
        w(f"用户图片 `user_image`：{im.get('name', '')}，{'必填' if im.get('required') else '选填'}，最多 1 张。{im.get('hint', '')}\n")
    else:
        w("用户图片：不使用。\n")

    w("## 4. 预置图\n")
    if flow.get("presets"):
        w("| 变量 | 文件 | 用途 |\n|---|---|---|")
        for p in flow["presets"]:
            w(f"| `{p['id']}` | `{p.get('file', '')}` | {p.get('purpose', '')} |")
        w("")
    else:
        w("无。\n")

    subj = flow.get("subject") or {}
    if subj.get("fixed"):
        w("**固定主角**\n")
        w("| 项目 | 内容 |\n|---|---|")
        w(f"| 主角类型 | {SUBJECT_TYPE_NAMES.get(subj.get('type'), subj.get('type', '未写'))} |")
        w(f"| 保留（每张都一样） | {'、'.join(subj.get('preserve') or []) or '未写'} |")
        w("| 变化（跟着内容走） | 表情、动作、姿势、手里的东西、场景、道具 |")
        w("")
        w("换主角时只换预置图，提示词不用改。\n")

    w("## 5. 步骤详情\n")
    for i, st in enumerate(flow.get("steps", []), 1):
        w(f"### 步骤 {i}：{st['name']}（`{st['id']}`）\n")
        w("| 项目 | 内容 |\n|---|---|")
        w(f"| 类型 | {TYPE_NAMES[st['type']]} |")
        if st.get("role"):
            w(f"| 页面角色 | `{st['role']}` {ROLE_NAMES.get(st['role'], '')} |")
        w(f"| 执行方式 | {describe_consume(st, rounds)} |")
        w(f"| 执行条件 | {('`' + st['when'] + '`') if st.get('when') else '总是执行'} |")
        if st["type"] == "image":
            w(f"| 参考方式 | {describe_ref(st)} |")
            w(f"| 图片尺寸 | {st.get('image_size', '—')} |")
        w(f"| 读取变量 | {'、'.join('`' + r + '`' for r in step_reads(st)) or '—'} |")
        w(f"| 输出变量 | `{st.get('output', {}).get('id', '')}` |")
        w(f"| 完成后暂停确认 | {'是' if st.get('confirm') else '否'} |\n")
        if st["type"] in ("text", "image"):
            if st["type"] == "image" and st.get("design"):
                w("**提示词设计说明**（这段提示词复刻的是什么）\n")
                w("| 层 | 内容 |\n|---|---|")
                for k, n in DESIGN_LAYERS + [("origin", "提示词来源")]:
                    if st["design"].get(k):
                        w(f"| {n} | {md_cell(st['design'][k])} |")
                w("")
            w("**完整提示词**\n")
            w("````text\n" + st.get("_prompt", "") + "\n````\n")
            slots = find_slots(st.get("_prompt", ""))
            if slots:
                w("**槽位**\n")
                w("| 槽位 | 类型 | 来源 | 填入规则 |\n|---|---|---|---|")
                for kind, expr, last in collapse_slots(slots):
                    src, fill = describe_slot(kind, expr, st, cat)
                    tag = {"text": "", "img": "img:", "img?": "img?:"}[kind]
                    label = f"`{{{{{tag}{expr}}}}}`"
                    if last:
                        label += f"～`{{{{{tag}{last}}}}}`"
                        fill = re.sub(r"取第 \d+ 项", "依次取第 " + re.search(r"\[(\d+)\]", expr).group(1)
                                      + "～" + re.search(r"\[(\d+)\]", last).group(1) + " 项", fill)
                    w(f"| {label} | {'文字' if kind == 'text' else ('可选图片' if kind == 'img?' else '图片')} | {src} | {fill} |")
                w("")
            n_img = sum(1 for kd, _ in slots if kd != "text")
            w(f"本步每次调用传入图片：{n_img} 张（上限 {MAX_IMAGES_PER_CALL}）。\n")
        w("**输出与连接规则**\n")
        w(describe_output(st, cat) + "\n")
        if st.get("checks"):
            w("**质量验收**\n")
            for c in st["checks"]:
                w(f"- {c}")
            w("")

    w("## 6. 变量连接总表\n")
    w("| 变量 | 类型 | 数量 | 由谁产生 | 被谁使用 |\n|---|---|---|---|---|")
    users = {}
    for st in flow.get("steps", []):
        for v in step_reads(st):
            users.setdefault(v, []).append(st["name"])
    for o_ in flow.get("outputs", []):
        users.setdefault(o_["var"].split(".")[0], []).append("最终交付")
    for v, info in cat.items():
        tname = {"list": "文字列表", "text": "文本", "json": "JSON", "json_list": "JSON 列表",
                 "image": "图片", "images": "图片列表", "text[]": "文本列表", "json[]": "JSON 列表",
                 "list[]": "列表的列表"}.get(info["type"], info["type"])
        size = fmt_range(info["size"]) if info.get("size") else "—"
        w(f"| `{v}` | {tname} | {size} | {var_source(v, cat)} | {'、'.join(dict.fromkeys(users.get(v, []))) or '—'} |")
    w("")

    w("## 7. 输出交付\n")
    w("| 顺序 | 内容 | 显示名 | 形式 |\n|---|---|---|---|")
    for i, o_ in enumerate(flow.get("outputs", []), 1):
        root = o_["var"].split(".")[0]
        t = cat.get(root, {}).get("type", "")
        form = "图片，可下载" if t in ("image", "images") else ("文字，一键复制" if o_.get("copy", True) else "文字")
        w(f"| {i} | `{o_['var']}` | {o_.get('name', '')} | {form} |")
    w("")

    w("## 8. 通用连接规则\n")
    w(GENERAL_RULES)

    w("## 9. 校验结果\n")
    if not issues:
        w("D1～D17 全部通过。\n")
    else:
        w("| 级别 | 编号 | 说明 |\n|---|---|---|")
        for lv, code, msg in issues:
            w(f"| {lv} | {code} | {msg} |")
        w("")
    return "\n".join(o)

# ---------------------------------------------------------------- 输入（运行阶段检查 R1～R3）

def load_inputs(flow, data, base_dir, run_dir=None):
    """把用户输入整理成变量，并做 R1～R3 检查。"""
    vars, problems = {}, []
    for t in flow.get("inputs", {}).get("texts", []):
        lines = split_lines(data.get(t["id"], ""))
        lo, hi = t["rows"]
        name = t.get("name", t["id"])
        if not lines:
            if t.get("required", True):
                problems.append(f"「{name}」没有填写。")
            vars[t["id"]] = []
            continue
        if not (lo <= len(lines) <= hi):
            problems.append(f"「{name}」需要 {fmt_range(t['rows'])} 行，现在是 {len(lines)} 行。")
        k = t.get("multiple_of")
        if k and len(lines) % k:
            problems.append(f"「{name}」的行数需要是 {k} 的倍数，现在是 {len(lines)} 行。")
        mc = t.get("max_chars")
        if mc:
            for i, ln in enumerate(lines, 1):
                if len(ln) > mc:
                    problems.append(f"「{name}」第 {i} 行有 {len(ln)} 个字，上限 {mc} 字。")
        vars[t["id"]] = lines
    im = flow.get("inputs", {}).get("image", {})
    if im.get("enabled"):
        p = data.get("user_image")
        if isinstance(p, list):
            problems.append("用户图片最多 1 张。")
            p = p[0] if p else None
        if p:
            path = p if os.path.isabs(p) else os.path.join(base_dir, p)
            if not os.path.exists(path):
                problems.append(f"找不到用户图片：{p}")
            elif (mimetypes.guess_type(path)[0] or "") not in ("image/png", "image/jpeg", "image/webp"):
                problems.append("用户图片只支持 png / jpg / webp。")
            else:
                vars["user_image"] = img_ref(copy_in(path, run_dir, "user_image"))
        elif im.get("required"):
            problems.append(f"需要上传图片「{im.get('name', '用户图片')}」。")
        if "user_image" not in vars:
            vars["user_image"] = None
    elif data.get("user_image"):
        problems.append("这个玩法不接收用户图片。")
    for pr in flow.get("presets", []):
        path = os.path.join(flow["_dir"], pr["file"])
        if not os.path.exists(path):
            problems.append(f"预置图 {pr['id']} 的文件不存在：{pr['file']}")
        else:
            vars[pr["id"]] = img_ref(copy_in(path, run_dir, pr["id"]))
    if problems:
        raise FlowError("输入需要修改：\n- " + "\n- ".join(problems))
    return vars


def copy_in(path, run_dir, name):
    if not run_dir:
        return os.path.abspath(path)
    dst_dir = os.path.join(run_dir, "inputs")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name + os.path.splitext(path)[1].lower())
    with open(path, "rb") as a, open(dst, "wb") as b:
        b.write(a.read())
    return dst


def read_input_file(flow, path, transfer=False):
    if path:
        return read_json(path), os.path.dirname(os.path.abspath(path))
    if transfer:
        if "transfer_input" not in flow:
            raise FlowError("flow.json 里没有 transfer_input（迁移测试输入）。")
        return flow["transfer_input"], flow["_dir"]
    if "sample_input" in flow:
        return flow["sample_input"], flow["_dir"]
    raise FlowError("没有输入：请用 --input 指定输入文件，或在 flow.json 里写 sample_input。")


def case_name(a):
    return "自定义输入" if a.input else ("迁移测试" if a.transfer else "还原测试")


# ---------------------------------------------------------------- 程序处理

def run_code(st, vars, run_dir):
    op = st.get("op", {})
    n = op.get("name")
    if n == "merge":
        out = []
        for l in op.get("lists", []):
            v = vars.get(l)
            if v:
                out += v if isinstance(v, list) else [v]
        return out
    if n == "pick":
        return eval_expr(op["path"], vars, {})
    if n == "slice":
        v = vars.get(op["list"]) or []
        return v[op.get("start", 1) - 1: op.get("end", len(v))]
    if n == "split_lines":
        return split_lines(to_text(vars.get(op["text"])))
    if n in ("compose", "overlay_text"):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            raise FlowError(f"{n} 需要 Pillow：请先运行 pip install pillow")
        path = os.path.join(run_dir, "images", f"{st['id']}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if n == "compose":
            ims = []
            for e in op.get("images", []):
                v = eval_expr(e, vars, {})
                for x in (v if isinstance(v, list) else [v]):
                    if is_img(x):
                        ims.append(Image.open(x["__img__"]).convert("RGB"))
            if not ims:
                raise FlowError(f"{st['name']}：没有可拼接的图片。")
            compose_images(ims, op, Image).save(path)
        else:
            base = eval_expr(op["image"], vars, {})
            if not is_img(base):
                raise FlowError(f"{st['name']}：{op['image']} 不是图片。")
            im = Image.open(base["__img__"]).convert("RGB")
            text = fill_prompt(op.get("text", ""), vars, {})["text"]
            draw_text(im, text, op, ImageDraw, ImageFont).save(path)
        return img_ref(path)
    raise FlowError(f"不认识的程序操作：{n}")


def compose_images(ims, op, Image):
    layout = op.get("layout", "row")
    gap = op.get("gap", 0)
    bg = op.get("background", "#FFFFFF")
    if layout == "row":
        h = min(i.height for i in ims)
        ims = [i.resize((round(i.width * h / i.height), h)) for i in ims]
        canvas = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) + 1), h + 2 * gap), bg)
        x = gap
        for i in ims:
            canvas.paste(i, (x, gap))
            x += i.width + gap
    elif layout == "column":
        w = min(i.width for i in ims)
        ims = [i.resize((w, round(i.height * w / i.width))) for i in ims]
        canvas = Image.new("RGB", (w + 2 * gap, sum(i.height for i in ims) + gap * (len(ims) + 1)), bg)
        y = gap
        for i in ims:
            canvas.paste(i, (gap, y))
            y += i.height + gap
    else:
        m = re.fullmatch(r"grid:(\d+)x(\d+)", layout)
        if not m:
            raise FlowError(f"compose 版式不认识：{layout}（row / column / grid:行x列）")
        rows, cols = int(m.group(1)), int(m.group(2))
        cw, ch = min(i.width for i in ims), min(i.height for i in ims)
        canvas = Image.new("RGB", (cols * cw + gap * (cols + 1), rows * ch + gap * (rows + 1)), bg)
        for n_, i in enumerate(ims[:rows * cols]):
            r, c = divmod(n_, cols)
            i = i.copy()
            i.thumbnail((cw, ch))
            canvas.paste(i, (gap + c * (cw + gap) + (cw - i.width) // 2, gap + r * (ch + gap) + (ch - i.height) // 2))
    if op.get("size"):
        W, H = op["size"]
        final = Image.new("RGB", (W, H), bg)
        canvas.thumbnail((W, H))
        final.paste(canvas, ((W - canvas.width) // 2, (H - canvas.height) // 2))
        canvas = final
    return canvas


def draw_text(im, text, op, ImageDraw, ImageFont):
    size = op.get("font_size", 48)
    font_path = op.get("font")
    candidates = [font_path] if font_path else [
        "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
        "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]
    font = None
    for c in candidates:
        if c and os.path.exists(c):
            font = ImageFont.truetype(c, size)
            break
    if font is None:
        raise FlowError("overlay_text 找不到中文字体，请在 op.font 里写字体文件路径。")
    d = ImageDraw.Draw(im)
    maxw = im.width - 2 * op.get("margin", 40)
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if d.textlength(cur + ch, font=font) > maxw and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    lh = int(size * 1.35)
    total = lh * len(lines)
    pos = op.get("position", "bottom")
    y = {"top": op.get("margin", 40), "center": (im.height - total) // 2}.get(pos, im.height - total - op.get("margin", 40))
    for ln in lines:
        x = (im.width - d.textlength(ln, font=font)) // 2
        d.text((x, y), ln, font=font, fill=op.get("color", "#000000"))
        y += lh
    return im


# ---------------------------------------------------------------- 执行

def loop_contexts(st, vars):
    """按 consume 生成每一轮的上下文。"""
    mode, k = parse_consume(st.get("consume"))
    if mode in ("once", "whole_list"):
        return [{}], False
    items = vars.get(st["over"])
    if isinstance(items, Pending) or st["over"] not in vars:
        return None, True
    items = items or []
    if mode == "per_item":
        n = len(items)
        return [{"item": it, "index": f"{i + 1:02d}", "total": n, "round": i}
                for i, it in enumerate(items)], True
    if len(items) % k:
        raise FlowError(f"「{st['name']}」要按每 {k} 项一组，但 {st['over']} 有 {len(items)} 项，不能整除。")
    n = len(items) // k
    return [{"group": items[i * k:(i + 1) * k], "index": f"{i + 1:02d}", "total": n, "round": i}
            for i in range(n)], True


def rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def to_state(v, base):
    if is_img(v):
        return {"__img__": rel(v["__img__"], base)}
    if isinstance(v, list):
        return [to_state(x, base) for x in v]
    return v


def from_state(v, base):
    if is_img(v):
        p = v["__img__"]
        return {"__img__": p if os.path.isabs(p) else os.path.join(base, p)}
    if isinstance(v, list):
        return [from_state(x, base) for x in v]
    return v


class Runner:
    def __init__(self, flow, run_dir, api=None, log=print):
        self.flow, self.run_dir, self.api, self.log = flow, run_dir, api, log
        self.state = {"flow": flow["_path"], "vars": {}, "done": [], "skipped": [], "paused_at": None}
        self.vars = {}

    # -- 状态
    def save(self):
        self.state["vars"] = {k: to_state(v, self.run_dir) for k, v in self.vars.items()}
        write_text(os.path.join(self.run_dir, "state.json"),
                   json.dumps(self.state, ensure_ascii=False, indent=2))

    @classmethod
    def resume(cls, run_dir, api=None, log=print):
        st = read_json(os.path.join(run_dir, "state.json"))
        flow = load_flow(st["flow"])
        r = cls(flow, run_dir, api, log)
        r.state = st
        r.vars = {k: from_state(v, run_dir) for k, v in st["vars"].items()}
        return r

    def step_by_id(self, sid):
        for s in self.flow["steps"]:
            if s["id"] == sid:
                return s
        raise FlowError(f"没有这个步骤：{sid}")

    def redo(self, sid):
        ids = [s["id"] for s in self.flow["steps"]]
        start = ids.index(self.step_by_id(sid)["id"])
        for s in self.flow["steps"][start:]:
            if s["id"] in self.state["done"]:
                self.state["done"].remove(s["id"])
            if s["id"] in self.state["skipped"]:
                self.state["skipped"].remove(s["id"])
            self.vars.pop(s.get("output", {}).get("id"), None)
        self.state["paused_at"] = None

    def reload_confirmed(self):
        """人工确认后，读回用户修改过的文件并重新检查。"""
        sid = self.state.get("paused_at")
        if not sid:
            return
        st = self.step_by_id(sid)
        out = st.get("output", {})
        oid = out.get("id")
        for ext in ("txt", "json"):
            p = os.path.join(self.run_dir, "edit", f"{oid}.{ext}")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    raw = f.read()
                mode, _ = parse_consume(st.get("consume"))
                try:
                    if mode in ("per_item", "grouped"):
                        val = json.loads(raw) if ext == "json" else split_lines(raw)
                        if len(val) != len(self.vars.get(oid) or []):
                            raise ParseError("修改后的条数与原来不同")
                    else:
                        val = parse_output(raw, out, self.vars)
                except ParseError as e:
                    raise FlowError(f"你修改的 {p} 不符合要求：{e}。请改好后再继续。")
                self.vars[oid] = val
                self.log(f"已读回修改：{oid}")
        self.state["paused_at"] = None

    def write_editable(self, st):
        oid = st["output"]["id"]
        v = self.vars.get(oid)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            p, body = os.path.join(self.run_dir, "edit", f"{oid}.txt"), "\n".join(v) + "\n"
        elif isinstance(v, str):
            p, body = os.path.join(self.run_dir, "edit", f"{oid}.txt"), v + "\n"
        else:
            p, body = os.path.join(self.run_dir, "edit", f"{oid}.json"), json.dumps(v, ensure_ascii=False, indent=2)
        write_text(p, body)
        return p

    # -- 执行
    def run(self, stop_before_images=False, until=None, no_pause=False):
        for st in self.flow["steps"]:
            sid = st["id"]
            if sid in self.state["done"] or sid in self.state["skipped"]:
                continue
            if stop_before_images and st["type"] == "image":
                self.save()
                self.log(f"\n已在第一个生图步骤「{st['name']}」之前停下。文字部分已完成。")
                return "stopped_before_images"
            cond = eval_when(st.get("when"), self.vars)
            oid = st.get("output", {}).get("id")
            if not cond:
                self.log(f"跳过「{st['name']}」（条件 {st['when']} 不满足）")
                self.state["skipped"].append(sid)
                if oid and oid not in self.vars:
                    self.vars[oid] = None
                self.save()
                continue
            self.log(f"▶ {st['name']}")
            self.vars[oid] = self.exec_step(st)
            self.state["done"].append(sid)
            self.save()
            if st.get("confirm") and not no_pause:
                p = self.write_editable(st)
                self.state["paused_at"] = sid
                self.save()
                self.log(f"\n⏸「{st['name']}」完成，等待确认。结果在 {p}，可以直接修改。")
                return "paused"
            if until and sid == until:
                self.log(f"\n已按要求在「{st['name']}」之后停下。")
                return "until"
        self.deliver()
        return "finished"

    def exec_step(self, st):
        if st["type"] == "code":
            return run_code(st, self.vars, self.run_dir)
        ctxs, loop = loop_contexts(st, self.vars)
        ref, n = parse_ref_mode(st.get("ref_mode"))
        results = [None] * len(ctxs)
        workers = max(1, int(os.environ.get("FLOW_CONCURRENCY", "3")))

        def one(i, extra=None):
            ctx = dict(ctxs[i])
            ctx.update(extra or {})
            results[i] = self.call(st, ctx, i, loop)

        if not loop or len(ctxs) == 1:
            one(0, {"prev": None, "anchor": None})
        elif ref == "chain":
            for i in range(len(ctxs)):
                one(i, {"prev": results[i - 1] if i else None})
        else:
            first = []
            if ref == "anchor":
                a = n - 1
                one(a, {"anchor": None})
                first = [a]
            extra = {"anchor": results[first[0]]} if first else {}
            rest = [i for i in range(len(ctxs)) if i not in first]
            with concurrent.futures.ThreadPoolExecutor(workers) as ex:
                for f in [ex.submit(one, i, extra) for i in rest]:
                    f.result()
        return results if loop else results[0]

    def call(self, st, ctx, i, loop):
        filled = fill_prompt(st["_prompt"], self.vars, ctx)
        tag = f"{st['id']}_{i + 1:02d}" if loop else st["id"]
        record = filled["text"]
        if filled["images"]:
            record += "\n\n---- 本次传入图片（按顺序）----\n" + "\n".join(
                f"图{j}：{rel(p, self.run_dir)}" for j, p in enumerate(filled["images"], 1))
        write_text(os.path.join(self.run_dir, "prompts", tag + ".txt"), record)
        if st["type"] == "image":
            data = self.api.image(filled["text"], filled["images"], st.get("image_size"))
            path = os.path.join(self.run_dir, "images", tag + ".png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            self.log(f"  ✓ {tag}.png")
            return img_ref(path)
        last = None
        for attempt in range(MAX_RETRY + 1):
            raw = self.api.chat(filled["parts"])
            write_text(os.path.join(self.run_dir, "raw", f"{tag}_try{attempt + 1}.txt"), raw)
            try:
                val = parse_output(raw, st["output"], self.vars)
                mo = st["output"].get("multiple_of")
                if mo and isinstance(val, list) and len(val) % mo:
                    raise ParseError(f"数量 {len(val)} 不是 {mo} 的倍数")
                self.log(f"  ✓ {tag}")
                return val
            except ParseError as e:
                last = e
                self.log(f"  ✗ {tag} 第 {attempt + 1} 次输出不合规：{e}")
        raise FlowError(f"「{st['name']}」重试 {MAX_RETRY} 次后仍不合规：{last}。原始输出见 raw/ 目录。")

    # -- 交付
    def deliver(self):
        d = os.path.join(self.run_dir, "deliver")
        os.makedirs(d, exist_ok=True)
        seq, items = 0, []
        for o in self.flow.get("outputs", []):
            v = eval_expr(o["var"], self.vars, {})
            name = o.get("name", o["var"])
            if isinstance(v, Pending) or v is None:
                continue
            imgs = v if isinstance(v, list) and v and all(is_img(x) for x in v) else ([v] if is_img(v) else None)
            if imgs:
                for j, im in enumerate(imgs, 1):
                    fn = f"{seq:02d}_{name}{f'{j:02d}' if len(imgs) > 1 else ''}.png"
                    with open(im["__img__"], "rb") as a, open(os.path.join(d, fn), "wb") as b:
                        b.write(a.read())
                    items.append(("img", name, fn))
                    seq += 1
            else:
                if isinstance(v, list) and o["var"].endswith("tags"):
                    txt = " ".join(x if str(x).startswith("#") else "#" + str(x) for x in v)
                else:
                    txt = to_text(v)
                items.append(("text", name, txt))
        case = self.state.get("case") or "运行"
        md = [f"# {self.flow['name']} · {case}结果\n"]
        body = []
        for kind, name, val in items:
            if kind == "img":
                md.append(f"![{name}]({val})")
                body.append(f'<figure><img src="{html.escape(val)}"><figcaption>{html.escape(val)}</figcaption></figure>')
            else:
                md.append(f"\n**{name}**\n\n{val}\n")
                body.append(f'<section><h3>{html.escape(name)} <button onclick="navigator.clipboard.writeText(this.parentNode.nextElementSibling.innerText)">复制</button></h3><pre>{html.escape(val)}</pre></section>')
        write_text(os.path.join(d, "result.md"), "\n".join(md))
        figs = "".join(b for b in body if b.startswith("<figure"))
        secs = "".join(b for b in body if b.startswith("<section"))
        ref_html = self.reference_html(d)
        page = ("<!doctype html><meta charset=utf-8><title>运行结果</title><style>"
                "body{font-family:system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 16px;color:#222}"
                ".g{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px}"
                "figure{margin:0}img{width:100%;border-radius:8px;border:1px solid #ddd}"
                "figcaption{font-size:12px;color:#666}pre{white-space:pre-wrap;background:#f6f6f6;padding:12px;border-radius:8px}"
                ".cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}.ref{background:#fbf7ef;padding:12px;border-radius:10px}"
                f"</style><h1>{html.escape(self.flow['name'])} · {html.escape(case)}</h1>")
        if ref_html:
            page += ("<p>上面是原作，下面是本次结果。还原测试看像不像；迁移测试看风格是否保持、画面是否跟着内容变。</p>"
                     f"<div class=ref><h2>原作</h2>{ref_html[0]}</div><h2>本次结果</h2><div class=g>{figs}</div>"
                     f"<div class=cols><div><h2>原作文字</h2>{ref_html[1]}</div><div><h2>本次文字</h2>{secs}</div></div>")
        else:
            page += f"<div class=g>{figs}</div>{secs}"
        write_text(os.path.join(d, "result.html"), page)
        self.log(f"\n✅ 完成。结果在 {d}（打开 result.html 查看）")

    def reference_html(self, d):
        """把参考案例原图复制到 deliver/reference/，返回 (图片网格, 原文) 两段 HTML；没有参考案例返回 None。"""
        ref = self.flow.get("reference")
        if not ref:
            return None
        figs = []
        for i, ri in enumerate(ref.get("images") or [], 1):
            src = os.path.join(self.flow["_dir"], ri.get("file") or "")
            cap = html.escape(f"原图{i}：{ri.get('note', '')}")
            if ri.get("file") and os.path.exists(src):
                os.makedirs(os.path.join(d, "reference"), exist_ok=True)
                fn = f"reference/{i:02d}{os.path.splitext(src)[1].lower()}"
                with open(src, "rb") as a, open(os.path.join(d, fn), "wb") as b:
                    b.write(a.read())
                figs.append(f'<figure><img src="{fn}"><figcaption>{cap}</figcaption></figure>')
            else:
                figs.append(f"<figure><figcaption>{cap}（未附图片）</figcaption></figure>")
        texts = "".join(f"<section><h3>{n}</h3><pre>{html.escape(str(v))}</pre></section>"
                        for n, v in (("标题", ref.get("title")), ("正文", ref.get("body")),
                                     ("标签", tags_text(ref.get("tags")) if ref.get("tags") else None)) if v)
        return f"<div class=g>{''.join(figs)}</div>", texts


# ---------------------------------------------------------------- 试跑前预览

CHEAP_OPS = ("merge", "pick", "slice", "split_lines")


def preview_contexts(st, vars, rounds):
    """预览用：列表已知时给出真实的每一轮，否则给一轮带占位的代表轮。"""
    try:
        ctxs, loop = loop_contexts(st, vars)
    except FlowError as e:
        return None, True, str(e)
    if ctxs is not None:
        return ctxs, loop, None
    mode, k = parse_consume(st.get("consume"))
    rep = {"item": Pending("item"), "index": "NN", "total": "N", "round": 0}
    if mode == "grouped":
        rep["group"] = [Pending(f"group[{j + 1}]") for j in range(k)]
    return [rep], True, f"轮数取决于 `{st['over']}`（预计 {fmt_range(rounds.get(st['id']))} 轮），下面是代表轮"


def plan(flow, vars, an, done=(), skipped=(), case=None):
    rounds = an["rounds"]
    o = [f"# 试跑预览：{flow['name']}" + (f"（{case}）" if case else "") + "\n",
         "下面是按当前输入，每一次模型调用**实际会发送**的提示词和图片。",
         "〔待生成：x〕表示这个值要等前面的步骤真正运行后才知道；【图N】表示此处传入第 N 张图片。\n"]
    est_calls = {"text": 0, "image": 0}
    unknown = False
    for n, st in enumerate(flow["steps"], 1):
        o.append(f"## 步骤 {n}：{st['name']}（`{st['id']}` · {TYPE_NAMES[st['type']]}）\n")
        oid = st.get("output", {}).get("id")
        if st["id"] in done:
            o.append(f"已完成，输出 `{oid}`：\n\n```text\n{preview_value(vars.get(oid))}\n```\n")
            continue
        if st["id"] in skipped:
            o.append("已跳过（条件不满足）。\n")
            continue
        cond = eval_when(st.get("when"), vars)
        if cond is False:
            o.append(f"不会执行：条件 `{st['when']}` 不满足。\n")
            vars.setdefault(oid, None)
            continue
        if isinstance(cond, Pending):
            o.append(f"是否执行取决于前面的结果（条件 `{st['when']}`）。\n")
        if st["type"] == "code":
            op = st.get("op", {})
            o.append(f"程序处理：{describe_op(st)}\n")
            if op.get("name") in CHEAP_OPS and cond is True:
                try:
                    probe = {k: v for k, v in vars.items() if not isinstance(v, Pending)}
                    needed = code_reads(st)
                    if all(r.split(".")[0].split("[")[0] in probe for r in needed):
                        vars[oid] = run_code(st, probe, None)
                        o.append(f"按当前输入的结果：\n\n```text\n{preview_value(vars[oid])}\n```\n")
                except FlowError as e:
                    o.append(f"⚠ {e}\n")
            continue
        ctxs, loop, note = preview_contexts(st, vars, rounds)
        if ctxs is None:
            o.append(f"⚠ {note}\n")
            continue
        if note:
            o.append(note + "。\n")
            unknown = True
        ref, an_n = parse_ref_mode(st.get("ref_mode"))
        rnd, rnd_errs = [], []
        for i, ctx in enumerate(ctxs):
            ctx = dict(ctx)
            if ref == "chain":
                ctx["prev"] = Pending("上一轮的图") if i else None
            elif ref == "anchor":
                ctx["anchor"] = None if i == an_n - 1 else Pending(f"第 {an_n} 轮的图")
            else:
                ctx.setdefault("prev", None)
                ctx.setdefault("anchor", None)
            try:
                filled = fill_prompt(st["_prompt"], vars, ctx)
            except FlowError as e:
                rnd_errs.append(f"⚠ 第 {i + 1} 轮无法填入：{e}\n")
                continue
            title = f"第 {ctx['index']} 轮" if loop and ctx.get("index") != "NN" else ("代表轮" if loop else "调用")
            imgs = [rel(p, flow['_dir']) if p else '〔待生成〕' for p in filled["images"]]
            rnd.append((title, imgs, filled["text"]))
            est_calls[st["type"]] += 1
        o.extend(rnd_errs)
        o.append(render_rounds(rnd))
    o.append("## 调用次数小计\n")
    o.append(f"- 文字模型：{est_calls['text']} 次{'（循环轮数未定，以上为已知部分）' if unknown else ''}")
    o.append(f"- 生图模型：{est_calls['image']} 次")
    o.append(f"- 按规格推算：文字 {fmt_range(an['est']['text_calls'])} 次，生图 {fmt_range(an['est']['image_calls'])} 次，出图 {fmt_range(an['est']['images'])} 张\n")
    return "\n".join(o)


def render_rounds(rnd):
    """输出循环的预览。

    同一循环里各轮提示词除了槽位填充值以外完全一样，逐轮全文打印会让预览
    膨胀好几倍（实测 8 轮里 87% 是重复内容），用户和建站方都很难看清真正
    会变化的部分。所以：第 1 轮给完整原文，其余各轮只列出与第 1 轮的差异。
    """
    if not rnd:
        return ""
    o = []
    title, imgs, text = rnd[0]
    o.append(f"### {title}\n")
    if imgs:
        o.append("传入图片（按顺序）：\n")
        for j, p in enumerate(imgs, 1):
            o.append(f"- 图{j}：{p}")
        o.append("")
    o.append("````text\n" + text + "\n````\n")

    if len(rnd) == 1:
        return "\n".join(o)

    base = text.split("\n")
    same_imgs = all(im == imgs for _, im, _ in rnd[1:])
    o.append(f"### 其余 {len(rnd) - 1} 轮\n")
    note = "提示词与第 1 轮完全相同" if same_imgs else "提示词与第 1 轮相同（传入图片见下表）"
    o.append(f"{note}，只有下面这些位置不同：\n")
    o.append("| 轮次 | 不同的行 | 这一轮填进去的 |")
    o.append("|---|---|---|")
    for title, im, text in rnd[1:]:
        lines = text.split("\n")
        shown = False
        for k, ln in enumerate(lines):
            if k < len(base) and ln == base[k]:
                continue
            o.append(f"| {title} | 第 {k + 1} 行 | {ln[:90]} |")
            shown = True
        if not same_imgs and im != imgs:
            o.append(f"| {title} | 传入图片 | {'、'.join(im)} |")
            shown = True
        if not shown:
            o.append(f"| {title} | （无差异） | |")
    o.append("")
    return "\n".join(o)


def preview_value(v):
    if v is None:
        return "（空）"
    if is_img(v):
        return "[图片] " + v["__img__"]
    if isinstance(v, list):
        return "\n".join(f"{i}. {preview_value(x)}" for i, x in enumerate(v, 1))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, indent=2)
    return str(v)


# ---------------------------------------------------------------- 命令

ENV_TEMPLATE = """# flowkit 配置。这个文件含密钥，不要发给别人，也不要写进流程规格。
# Base URL：兼容 OpenAI 接口的地址，例如 https://api.openai.com/v1 或中转服务地址
FLOW_BASE_URL=
FLOW_API_KEY=
# 文字模型：必须支持看图，例如 gpt-4o / gpt-4.1 / claude-sonnet-5 等（以服务商提供的名称为准）
FLOW_TEXT_MODEL=
# 生图模型：默认 gpt-image-2
FLOW_IMAGE_MODEL=gpt-image-2
# 单次请求超时（秒）和并发数
FLOW_TIMEOUT=300
FLOW_CONCURRENCY=3
"""


def mask(key):
    return key[:4] + "…" + key[-4:] if key and len(key) > 10 else "（已设置）" if key else "（未设置）"


def cmd_validate(a):
    flow = load_flow(a.flow)
    issues, an = validate(flow)
    est = an["est"]
    print(f"玩法：{flow.get('name')}（{flow.get('id')}）")
    if est.get("images"):
        print(f"出图 {fmt_range(est['images'])} 张；文字调用 {fmt_range(est['text_calls'])} 次；生图调用 {fmt_range(est['image_calls'])} 次")
    if not issues:
        print("✓ D1～D17 全部通过")
        return 0
    for lv, code, msg in issues:
        print(f"[{lv}] {code} {msg}")
    return 1 if any(lv == "错误" for lv, _, _ in issues) else 0


def cmd_render(a):
    flow = load_flow(a.flow)
    issues, an = validate(flow)
    out = a.output or os.path.join(flow["_dir"], "spec.md")
    write_text(out, render(flow, issues, an))
    print(f"已生成 {out}")
    errs = [i for i in issues if i[0] == "错误"]
    if errs:
        print(f"注意：有 {len(errs)} 个错误，见文末“校验结果”。")
    return 0


def cmd_plan(a):
    if a.run_dir:
        r = Runner.resume(a.run_dir, log=lambda m: None)
        r.reload_confirmed()        # 人工确认暂停中：按用户修改后的内容预览（不写回 state）
        flow, vars = r.flow, dict(r.vars)
        done, skipped = r.state["done"], r.state["skipped"]
        default_out = os.path.join(a.run_dir, "plan.md")
    else:
        flow = load_flow(a.flow)
        data, base = read_input_file(flow, a.input, a.transfer)
        vars = load_inputs(flow, data, base)
        done, skipped = (), ()
        default_out = os.path.join(flow["_dir"], "plan.md")
    issues, an = validate(flow)
    errs = [i for i in issues if i[0] == "错误"]
    if errs:
        raise FlowError("规格还有错误，请先修正：\n" + "\n".join(f"- {c} {m}" for _, c, m in errs))
    out = a.output or default_out
    write_text(out, plan(flow, vars, an, done, skipped, case_name(a) if not a.run_dir else None))
    print(f"已生成预览 {out}")
    return 0


def make_logger(run_dir):
    path = os.path.join(run_dir, "run.log")

    def log(msg):
        print(msg, flush=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%H:%M:%S}] {msg}\n")
    return log


def cmd_run(a):
    if a.resume:
        load_env_file(os.getcwd(), a.resume)
        st = read_json(os.path.join(a.resume, "state.json"))
        load_env_file(os.path.dirname(st["flow"]))
        log = make_logger(a.resume)
        r = Runner.resume(a.resume, None, log)
        if a.redo:
            r.redo(a.redo)
            log(f"重新执行「{r.step_by_id(a.redo)['name']}」及之后的步骤")
        else:
            r.reload_confirmed()
    else:
        if not a.flow:
            raise FlowError("请指定 flow.json，或用 --resume 继续已有的运行目录。")
        flow = load_flow(a.flow)
        load_env_file(os.getcwd(), flow["_dir"])
        issues, _ = validate(flow)
        errs = [i for i in issues if i[0] == "错误"]
        if errs:
            raise FlowError("规格还有错误，不能运行：\n" + "\n".join(f"- {c} {m}" for _, c, m in errs))
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        case = case_name(a)
        tag = {"还原测试": "restore", "迁移测试": "transfer"}.get(case, "custom")
        run_dir = os.path.abspath(a.out or os.path.join(os.getcwd(), "flowkit_runs", f"{flow['id']}_{tag}_{stamp}"))
        os.makedirs(run_dir, exist_ok=True)
        log = make_logger(run_dir)
        data, base = read_input_file(flow, a.input, a.transfer)
        r = Runner(flow, run_dir, None, log)
        r.state["case"] = case
        r.vars = load_inputs(flow, data, base, run_dir)
        write_text(os.path.join(run_dir, "input.json"), json.dumps(data, ensure_ascii=False, indent=2))
        r.save()
        log(f"运行目录：{run_dir}（{case}）")
    r.api = Api(r.flow)
    t0 = time.time()
    status = r.run(a.stop_before_images, a.until, a.no_pause)
    r.log(f"用时 {time.time() - t0:.0f} 秒")
    if status != "finished":
        here = os.path.abspath(__file__)
        r.log(f"继续运行：python3 {here} run --resume {r.run_dir}")
        if status == "stopped_before_images":
            r.log(f"查看完整生图提示词：python3 {here} plan --run-dir {r.run_dir}")
    return 0


def cmd_check_api(a):
    load_env_file(os.getcwd(), *( [os.path.dirname(os.path.abspath(a.flow))] if a.flow else []))
    api = Api(load_flow(a.flow) if a.flow else None)
    print(f"Base URL：{api.base}\nAPI Key：{mask(api.key)}\n文字模型：{api.text_model or '（未设置）'}\n生图模型：{api.image_model}")
    reply = api.chat([("text", "只回复两个字：正常")])
    print(f"✓ 文字模型可用，回复：{reply.strip()[:50]}")
    if a.image:
        data = api.image("一个红色的圆形，白色背景，简洁扁平", [], "1024x1024")
        p = os.path.join(os.getcwd(), "flowkit_check.png")
        with open(p, "wb") as f:
            f.write(data)
        print(f"✓ 生图模型可用，测试图：{p}")
    return 0


def cmd_init_env(a):
    p = os.path.join(a.dir or os.getcwd(), "flowkit.env")
    if os.path.exists(p):
        print(f"{p} 已存在，未覆盖。")
        return 0
    write_text(p, ENV_TEMPLATE)
    print(f"已生成 {p}，请填写 FLOW_BASE_URL、FLOW_API_KEY、FLOW_TEXT_MODEL。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flowkit", description="AI 图文素材流程工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate", help="检查 flow.json")
    p.add_argument("flow")
    p = sub.add_parser("render", help="生成《流程规格》spec.md")
    p.add_argument("flow")
    p.add_argument("-o", "--output")
    p = sub.add_parser("plan", help="试跑前预览每次调用的提示词")
    p.add_argument("flow", nargs="?")
    p.add_argument("--input")
    p.add_argument("--transfer", action="store_true", help="用 transfer_input（迁移测试输入）")
    p.add_argument("--run-dir")
    p.add_argument("-o", "--output")
    p = sub.add_parser("run", help="真实运行流程")
    p.add_argument("flow", nargs="?")
    p.add_argument("--input")
    p.add_argument("--transfer", action="store_true", help="用 transfer_input（迁移测试输入）")
    p.add_argument("--out")
    p.add_argument("--resume")
    p.add_argument("--redo")
    p.add_argument("--stop-before-images", action="store_true")
    p.add_argument("--until")
    p.add_argument("--no-pause", action="store_true")
    p = sub.add_parser("check-api", help="测试接口配置")
    p.add_argument("flow", nargs="?")
    p.add_argument("--image", action="store_true", help="同时生成一张测试图（会产生费用）")
    p = sub.add_parser("init-env", help="生成 flowkit.env 模板")
    p.add_argument("--dir")
    a = ap.parse_args(argv)
    if a.cmd == "plan" and not a.flow and not a.run_dir:
        ap.error("plan 需要 flow.json 或 --run-dir")
    fn = {"validate": cmd_validate, "render": cmd_render, "plan": cmd_plan, "run": cmd_run,
          "check-api": cmd_check_api, "init-env": cmd_init_env}[a.cmd]
    try:
        return fn(a)
    except FlowError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
