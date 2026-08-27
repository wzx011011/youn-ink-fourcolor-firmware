"""AI 对话看板 — dify / 智谱 GLM 双后端 → PIL 渲染推屏。

Provider 由「设置」页选择,凭据持久化在 /data/schedule_config.json 同层:
    ai_provider    : "zhipu" | "dify"
    zhipu_api_key : 智谱开放平台 Key (格式如 "xxxx.yyyy")
    zhipu_model   : 默认 glm-5.3-flash
    dify_api_key  : dify 应用 Key (app- 开头; dify 经本机 nginx 28080)

流程:NAS 网页输入问题 → /api/board/chat → 调用所选后端 → 回复文本
渲染到墨水屏。未配置时返回引导文案且不推屏。
"""

import json
import os
import time
import urllib.request

from board.pil_renderer import (
    pil_renderer, C_BLACK, C_WHITE, C_RED, C_YELLOW, SCREEN_W, SCREEN_H,
)
from board.config_store import CONFIG_PATH

# dify 默认经其 nginx 暂露在本机 28080;换端口改这里
DIFY_BASE = os.environ.get("DIFY_BASE", "http://127.0.0.1:28080")
# 智谱开放平台(OpenAI 兼容格式)
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_ZHIPU_MODEL = os.environ.get("ZHIPU_MODEL", "glm-5.3-flash")


def _load_cfg() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(update: dict):
    cfg = _load_cfg()
    cfg.update(update)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ===== 供 app.py 调用的配置接口 =====

def get_ai_config() -> dict:
    """Return current AI backend config for the web settings page."""
    cfg = _load_cfg()
    provider = cfg.get("ai_provider", "zhipu" if cfg.get("zhipu_api_key") else "dify")
    return {
        "provider": provider,
        "configured": bool(cfg.get("zhipu_api_key") or cfg.get("dify_api_key")),
        "has_zhipu": bool(cfg.get("zhipu_api_key")),
        "has_dify": bool(cfg.get("dify_api_key")),
        "zhipu_model": cfg.get("zhipu_model", DEFAULT_ZHIPU_MODEL),
    }


def set_ai_config(provider: str = None, zhipu_key: str = None,
                  zhipu_model: str = None, dify_key: str = None):
    update = {}
    if provider is not None and provider in ("zhipu", "dify"):
        update["ai_provider"] = provider
    if zhipu_key is not None:
        update["zhipu_api_key"] = zhipu_key.strip()
    if zhipu_model is not None and zhipu_model.strip():
        update["zhipu_model"] = zhipu_model.strip()
    if dify_key is not None:
        update["dify_api_key"] = dify_key.strip()
    if update:
        _save_cfg(update)


def _post_json(url, body, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ===== Backends =====

def _ask_zhipu(question: str, cfg: dict) -> dict:
    """智谱 GLM 直连(open.bigmodel.cn, OpenAI chat/completions 格式)。"""
    key = (cfg.get("zhipu_api_key") or "").strip()
    if not key:
        return {"ok": False, "error": "未配置智谱 API Key",
                "hint": "在 NAS 设置页粘贴后重试"}
    model = cfg.get("zhipu_model") or DEFAULT_ZHIPU_MODEL
    try:
        raw = _post_json(
            ZHIPU_URL,
            {"model": model,
             "messages": [
                 {"role": "system",
                  "content": "你是一个墨水屏看板助手。回答务必简短精炼,"
                             "不超过200字,分点叙述优先。"},
                 {"role": "user", "content": question},
             ],
             "temperature": 0.7},
            {"Authorization": f"Bearer {key}",
             "Content-Type": "application/json"},
            timeout=90,
        )
        answer = ((raw.get("choices") or [{}])[0].get("message") or {}) \
            .get("content", "").strip()
    except Exception as e:
        return {"ok": False, "error": f"智谱调用失败: {e}",
                "hint": "", "answer": "", "question": question}
    return {"ok": True, "answer": answer,
            "question": question, "model": model}


def _ask_dify(question: str, cfg: dict) -> dict:
    """dify 应用的 blocking chat-messages。"""
    key = (cfg.get("dify_api_key") or "").strip()
    if not key:
        return {"ok": False, "error": "未配置 DIFY_API_KEY",
                "hint": "在 NAS 设置页粘贴 dify 应用 Key 后重试"}
    body = json.dumps({
        "inputs": {}, "query": question.strip(),
        "response_mode": "blocking", "user": "eink-panel",
    }).encode()
    try:
        raw = _post_json(
            f"{DIFY_BASE}/v1/chat-messages", body,
            {"Authorization": f"Bearer {key}",
             "Content-Type": "application/json"},
            timeout=60,
        )
        answer = (raw.get("answer") or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"dify 调用失败: {e}",
                "hint": "", "answer": "", "question": question}
    return {"ok": True, "answer": answer, "question": question}


# ============================================================
# get_data: registry 标准签名(get_data() 引导态不含提问;
# /api/board/chat 会带 question 触发真实调用)
# ============================================================

def get_data(question: str = "") -> dict:
    cfg = _load_cfg()
    provider = cfg.get("ai_provider") or (
        "zhipu" if cfg.get("zhipu_api_key") else "dify")

    if not question.strip():
        has_any = bool(cfg.get("zhipu_api_key") or cfg.get("dify_api_key"))
        return {
            "ok": False,
            "error": "AI 对话看板",
            "hint": "" if has_any else "先在 NAS 设置页配置 AI 后端",
            "answer": "", "question": "",
        }

    if provider == "dify":
        result = _ask_dify(question.strip(), cfg)
    else:
        result = _ask_zhipu(question.strip(), cfg)
    result.setdefault("question", question.strip())
    return result


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
            draw_red.text((SCREEN_W - pad - 64, SCREEN_H - 22),
                          "(已截断)", font=f16, fill=0)

    return pil_renderer.render(layout)
