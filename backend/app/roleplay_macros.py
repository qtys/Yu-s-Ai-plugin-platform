"""Bounded request-local macro subset. Never evaluates code or persists variables."""
import random
import re

BASE = {"char", "user", "description", "personality", "scenario", "persona", "original", "lastusermessage"}
SUPPORTED = BASE | {"setvar", "getvar", "addvar", "incvar", "decvar", "hasvar", "deletevar", "random", "roll", "//", "trim", "newline"}


def arguments(raw):
    raw = raw.strip()
    if raw.startswith("//"):
        return "//", [raw[2:]]
    parts = re.split(r"\s*::\s*", raw)
    head = re.split(r"\s+|:(?!:)", parts[0], maxsplit=1)
    return head[0].lower(), head[1:] + parts[1:]


def macro_names(text):
    # Includes outer names whose arguments contain nested macros.
    return sorted(set(m.group(1).lower() for m in re.finditer(r"{{\s*([^\s:{}]+)", text)))


def unsupported(text):
    return [name for name in macro_names(text) if name not in SUPPORTED and not name.startswith("//")]


class MacroSession:
    def __init__(self, values=None, seed=None):
        self.values = values or {}
        self.variables = {}
        self.random = random.Random(seed)
        self.operations = 0
        self.output_size = 0

    def render(self, text, warnings):
        missing = unsupported(text)
        if missing:
            warnings.append("模块未发送：不支持的宏/占位符 " + ", ".join(missing))
            return ""
        # Recursive arguments are evaluated left-to-right, not in global regex passes.
        def expand(source, depth=0):
            if depth > 16:
                raise ValueError("宏嵌套超过 16 层")
            output, cursor, size = [], 0, 0
            while True:
                start = source.find("{{", cursor)
                if start < 0:
                    output.append(source[cursor:])
                    break
                output.append(source[cursor:start])
                size += start - cursor
                pos, nesting = start + 2, 1
                while nesting and pos < len(source):
                    if source.startswith("{{", pos):
                        nesting += 1; pos += 2
                    elif source.startswith("}}", pos):
                        nesting -= 1; pos += 2
                    else:
                        pos += 1
                if nesting:
                    raise ValueError("宏括号未闭合")
                raw = source[start + 2:pos - 2]
                # Comments must not execute nested variable writes.
                name, args = arguments(raw)
                if name != "//":
                    name, args = arguments(expand(raw, depth + 1))
                self.operations += 1
                if self.operations > 10000:
                    raise ValueError("宏操作超过 10000 次")
                result = self.evaluate(name, args)
                output.append(result)
                size += len(result)
                if size > 300000:
                    raise ValueError("宏展开内容超过 300000 字符")
                cursor = pos
            result = "".join(output)
            if len(result) > 300000:
                raise ValueError("宏展开内容超过 300000 字符")
            return result
        # Failed blocks roll back both variables and random state.
        snapshot, rng = self.variables.copy(), self.random.getstate()
        try:
            result = expand(text)
            if self.output_size + len(result) > 1500000:
                raise ValueError("本次请求宏展开总量超过 1500000 字符")
            self.output_size += len(result)
            return result
        except ValueError as exc:
            self.variables = snapshot
            self.random.setstate(rng)
            warnings.append(f"模块未发送：{exc}")
            return ""

    def evaluate(self, name, args):
        if name in BASE:
            return str(self.values.get(name, ""))
        if name in ("//", "trim"):
            return ""
        if name == "newline":
            return "\n"
        if name == "random":
            options = args if len(args) > 1 else (args[0].split(",") if args else [])
            if not options:
                raise ValueError("random 缺少候选内容")
            return self.random.choice(options).strip()
        if name == "roll":
            match = re.fullmatch(r"(\d*)d(\d+)([+-]\d+)?", args[0].strip() if args else "1d6", re.I)
            if not match:
                raise ValueError("roll 仅支持 NdM±K")
            count, sides, offset = int(match[1] or 1), int(match[2]), int(match[3] or 0)
            if not 1 <= count <= 100 or not 1 <= sides <= 1000000:
                raise ValueError("骰子范围过大")
            return str(sum(self.random.randint(1, sides) for _ in range(count)) + offset)
        if not args or not args[0] or len(args[0]) > 200:
            raise ValueError("变量名为空或过长")
        key = args[0]
        old = self.variables.get(key, "")
        if name == "getvar":
            return old
        if name == "hasvar":
            return "true" if key in self.variables else "false"
        if name == "deletevar":
            self.variables.pop(key, None)
            return ""
        if name in ("setvar", "addvar"):
            if len(args) < 2:
                raise ValueError(f"{name} 缺少值（不支持作用域块语法）")
            value = "::".join(args[1:])
            if name == "addvar":
                try:
                    value = str(int(old or 0) + int(value))
                except ValueError:
                    value = old + value
        elif name in ("incvar", "decvar"):
            try:
                value = str(int(old or 0) + (1 if name == "incvar" else -1))
            except ValueError as exc:
                raise ValueError("递增/递减变量不是整数") from exc
        else:
            raise ValueError(f"不支持的宏 {name}")
        if len(value) > 100000 or (key not in self.variables and len(self.variables) >= 1000):
            raise ValueError("变量容量超过限制")
        self.variables[key] = value
        return value if name in ("incvar", "decvar") else ""
