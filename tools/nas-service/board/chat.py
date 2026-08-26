"""AI 对话看板 — NAS 上的 dify → PIL 渲染推屏。

流程:NAS 网页输入问题 → /api/board/render {board:"chat"} → 本模块调
dify chat-messages API → 回复文本分页渲染到墨水屏。

dify 应用的 API Key 从环境变量 DIFY_API_KEY 读取(NAS 网页「设置」页可写入,
持久化在 schedule_config.json 同目录)。未配置时模板显示引导文案。
"""

import json
import os
import urllib.request
from pathlib import Path

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
)
from board.config_store import CONFIG_PATH

# dify 默认经其 nginx 暴露在本机 28080;换端口改这里
DIFY_BASE = os.environ.get("DIFY_BASE", "http://127.0.0.1:28080")


def _api_key() -> str:
    """Read the dify app API key from config file (web-writable)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return (json.load(f).get("dify_api_key") or "").strip()
    except Exception:
        return ""


def set_api_key(key: str):
    """Persist the dify key alongside schedules."""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["dify_api_key"] = key.strip()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_data(question: str = "") -> dict:
    """Ask dify; returns {ok, answer, question} for render."""
    key = _api_key()
    if not key:
        return {"ok": False,
                "error": "未配置 DIFY_API_KEY",
                "hint": "在 NAS 设置页填入后重试",
                "answer": "", "question": question}
    if not question.strip():
        return {"ok": False, "error": "请输入问题",
                "hint": "", "answer": "", "question": ""}

    body = json.dumps({
        "inputs": {}, "query": question.strip(),
        "response_mode": "blocking", "user": "eink-panel",
    }).encode()
    req = urllib.request.Request(
        f"{DIFY_BASE}/v1/chat-messages", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"dify 调用失败: {e}",
                "hint": "", "answer": "", "question": question}

    return {
        "ok": True,
        "answer": (raw.get("answer") or "").strip(),
        "question": question.strip(),
    }


# ============================================================
# Template: text — Q on top, A below (wrapped, e-ink friendly)
# ============================================================

def render_text(data: dict):
    """问答版:顶部黄条问句(截断),下方回复正文(自动折行)。"""

    def layout(draw_rgb, draw_text, draw_red, font):
        f24 = font(24)
        f16 = font(16)

        if not data.get("ok"):
            draw_rgb.rectangle([0, 0, SCREEN_W, 44], fill=C_YELLOW)
            draw_text.text((16, 10), data.get("error", "出错了"), font=f24, fill=0)
            hint = data.get("hint", "")
            if hint:
                draw_text.text((16, 56), hint, font=f16, fill=0)
            # 显示问题回显
            q = "问:" + data.get("question", "")[:14]
            draw_text.text((16, 90), q, font=f16, fill=0)
            return

        pad = 16
        max_chars = 22   # 16px 中文一行约 23 字,留边取 22
        max_lines = 12

        # ===== 问句条(黄底)=====
        draw_rgb.rectangle([0, 0, SCREEN_W, 44], fill=C_YELLOW)
        q = "Q " + (data.get("question", "")[:18])
        draw_text.text((pad, 10), q, font=f24, fill=0)

        # ===== 回复正文:简单逐字折行 =====
        answer = data.get("answer", "").replace("\r", "")
        lines = []
        cur = ""
        for ch in answer:
            if ch == "\n":
                lines.append(cur); cur = ""
                if len(lines) >= max_lines: break
                continue
            cur += ch
            if len(cur) >= max_chars:
                lines.append(cur); cur = ""
                if len(lines) >= max_lines: break
        if cur and len(lines) < max_lines:
            lines.append(cur)

        y = 58
        total_lines = len(lines)
        truncated = False
        if len(answer.replace("\n", "")) > max_chars * max_lines + 5:
            total_lines += 1
            truncated = True
        line_h = min(26, (SCREEN_H - y - 30) // max(total_lines, 1))
        draw_text.text((pad, y), "A", font=f16, fill=0)
        y += 20
        shown = 0
        for ln in lines[:max_lines]:
            draw_text.text((pad + 4, y), ln, font=f16, fill=0)
            y += line_h
            shown += 1
        if truncated:
            draw_text.text((SCREEN_W - pad - 64, SCREEN_H - 22),
                           "(已截断)", font=f16, fill=0)

    return pil_renderer.render(layout)
