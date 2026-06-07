# -*- coding: utf-8 -*-

def format_command_result(code: int, out: str, err: str) -> str:
        parts = []
        if out.strip():
            parts.append(out.strip())
        if err.strip():
            parts.append("[stderr]\n" + err.strip())
        if not parts:
            parts.append(f"命令执行完成，exit_code={code}")
        return "\n\n".join(parts)
def parse_block(text: str, start_marker: str, end_marker: str) -> str:
        start = text.find(start_marker)
        end = text.find(end_marker)
        if start == -1 or end == -1 or end < start:
            return ""
        start += len(start_marker)
        return text[start:end].strip("\n")
