from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from dotenv import set_key
from PIL import Image


ENV_PATH = Path(".env")
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MINIMAX_OPENAI_BASE_URL = "https://api.minimax.io/v1"
DEFAULT_MINIMAX_ANTHROPIC_BASE_URL = "https://api.minimax.io/anthropic"
DASHSCOPE_BASE_URLS = {
    "中国北京": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "国际新加坡": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "美国弗吉尼亚": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "中国香港": "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
}


@dataclass(frozen=True)
class ApiConfig:
    # Generic role-based settings. The legacy fields below remain supported.
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_model: str | None = None
    vision_protocol: str | None = None
    planner_api_key: str | None = None
    planner_base_url: str | None = None
    planner_model: str | None = None
    planner_protocol: str | None = None
    dashscope_api_key: str | None = None
    dashscope_base_url: str | None = None
    qwen_model: str | None = None
    minimax_api_key: str | None = None
    minimax_base_url: str | None = None
    minimax_model: str | None = None
    minimax_protocol: str | None = None


def env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def save_api_config(config: ApiConfig) -> str:
    ENV_PATH.touch(exist_ok=True)
    dashscope_base_url = normalize_dashscope_base_url(config.dashscope_base_url)
    minimax_protocol = normalize_minimax_protocol(config.minimax_protocol, config.minimax_base_url)
    minimax_base_url = normalize_minimax_base_url(config.minimax_base_url, minimax_protocol)
    values = {
        "MECHCAD_VISION_API_KEY": config.vision_api_key or config.dashscope_api_key or "",
        "MECHCAD_VISION_BASE_URL": normalize_role_base_url(config.vision_base_url or config.dashscope_base_url),
        "MECHCAD_VISION_MODEL": config.vision_model or config.qwen_model or "",
        "MECHCAD_VISION_PROTOCOL": normalize_role_protocol(config.vision_protocol, config.vision_base_url),
        "MECHCAD_PLANNER_API_KEY": config.planner_api_key or config.minimax_api_key or "",
        "MECHCAD_PLANNER_BASE_URL": normalize_role_base_url(config.planner_base_url or config.minimax_base_url),
        "MECHCAD_PLANNER_MODEL": config.planner_model or config.minimax_model or "",
        "MECHCAD_PLANNER_PROTOCOL": normalize_role_protocol(config.planner_protocol, config.planner_base_url),
        "DASHSCOPE_API_KEY": config.dashscope_api_key or "",
        "DASHSCOPE_BASE_URL": dashscope_base_url,
        "QWEN_MODEL": config.qwen_model or "",
        "MINIMAX_API_KEY": config.minimax_api_key or "",
        "MINIMAX_BASE_URL": minimax_base_url,
        "MINIMAX_MODEL": config.minimax_model or "",
        "MINIMAX_API_PROTOCOL": minimax_protocol,
    }
    for key, value in values.items():
        set_key(str(ENV_PATH), key, value)
        os.environ[key] = value
    return (
        "已保存到 .env。本次运行已立即生效，之后重启也会读取这些配置。\n\n"
        f"- DashScope base url: `{dashscope_base_url}`\n"
        f"- MiniMax protocol: `{minimax_protocol}`\n"
        f"- MiniMax base url: `{minimax_base_url}`"
    )


def test_qwen_config(config: ApiConfig) -> str:
    api_key = _required(config.vision_api_key or config.dashscope_api_key, "MECHCAD_VISION_API_KEY")
    protocol = normalize_role_protocol(config.vision_protocol, config.vision_base_url)
    base_url = normalize_role_base_url(config.vision_base_url or config.dashscope_base_url)
    model = config.vision_model or config.qwen_model or "qwen2.5-vl-32b-instruct"
    image_b64 = _tiny_png_base64()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Return strict JSON: {\"ok\": true}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 64,
    }
    if protocol == "anthropic":
        anthropic_payload = {
            "model": model,
            "system": "Reply with exactly: {\"ok\": true}",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "Return strict JSON: {\"ok\": true}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            ]}],
            "temperature": 0,
            "max_tokens": 64,
        }
        return _post_anthropic_message("Vision model", api_key, base_url, anthropic_payload, timeout=45)
    return _post_chat_completion("Vision model", api_key, base_url, payload, timeout=45)


def test_minimax_config(config: ApiConfig) -> str:
    api_key = _required(config.planner_api_key or config.minimax_api_key, "MECHCAD_PLANNER_API_KEY")
    protocol = normalize_role_protocol(config.planner_protocol or config.minimax_protocol, config.planner_base_url or config.minimax_base_url)
    base_url = normalize_role_base_url(config.planner_base_url or config.minimax_base_url)
    model = config.planner_model or config.minimax_model or "MiniMax-M2.7"
    if protocol == "anthropic":
        payload = {
            "model": model,
            "system": "You are a connectivity test. Reply with exactly: ok",
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "temperature": 0.1,
            "max_tokens": 16,
        }
        return _post_anthropic_message("Planner model", api_key, base_url, payload, timeout=60)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "temperature": 0,
        "max_tokens": 16,
    }
    return _post_chat_completion("Planner model", api_key, base_url, payload, timeout=60)


def _post_chat_completion(name: str, api_key: str, base_url: str, payload: dict, timeout: int) -> str:
    response, tried_url, retry_note = _post_openai_chat_with_v1_retry(name, api_key, base_url, payload, timeout)
    if isinstance(response, str):
        return response
    if not response.ok:
        body = response.text[:800]
        hint = _http_error_hint(name, response.status_code, base_url, payload["model"])
        return f"{name} 连接失败：HTTP {response.status_code}\n\nurl: `{tried_url}`\n{hint}\n\n{body}"
    try:
        data = response.json()
    except ValueError as exc:
        body = response.text[:800]
        content_type = response.headers.get("content-type", "unknown")
        return (
            f"{name} 连接失败：API 返回的不是 JSON。\n\n"
            f"base url: `{base_url}`\n"
            f"tried url: `{tried_url}`\n"
            f"model: `{payload['model']}`\n"
            f"content-type: `{content_type}`\n\n"
            "如果返回 HTML，通常是 Base URL 少了 `/v1`，或填成了网站首页而不是 OpenAI-compatible API 根地址。\n\n"
            f"body:\n{body}\n\n"
            f"parse error: {exc}"
        )
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    usage_text = f"\n\nusage: `{usage}`" if usage else ""
    return f"{name} 连接成功。模型 `{payload['model']}` 返回：\n\n{content[:500]}{retry_note}{usage_text}"


def _post_openai_chat_with_v1_retry(
    name: str,
    api_key: str,
    base_url: str,
    payload: dict,
    timeout: int,
) -> tuple[requests.Response, str, str] | tuple[str, str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    urls = [f"{base_url}/chat/completions"]
    if not base_url.rstrip("/").endswith("/v1"):
        urls.append(f"{base_url}/v1/chat/completions")
    last_response: requests.Response | None = None
    tried_url = urls[0]
    for index, url in enumerate(urls):
        tried_url = url
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            return _connection_error_hint(name, base_url, exc), tried_url, ""
        last_response = response
        content_type = response.headers.get("content-type", "").lower()
        if response.ok and "json" in content_type:
            note = "\n\n已自动补试 `/v1` 路径。" if index == 1 else ""
            return response, tried_url, note
        if response.ok and "text/html" in content_type and index == 0 and len(urls) > 1:
            continue
        break
    assert last_response is not None
    return last_response, tried_url, ""


def _post_anthropic_message(name: str, api_key: str, base_url: str, payload: dict, timeout: int) -> str:
    url = f"{base_url}/v1/messages"
    try:
        response = requests.post(
            url,
            headers={
                "X-Api-Key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return _connection_error_hint(name, base_url, exc)
    if not response.ok:
        body = response.text[:800]
        hint = _http_error_hint(name, response.status_code, base_url, payload["model"])
        return f"{name} 连接失败：HTTP {response.status_code}\n\n{hint}\n\n{body}"
    try:
        data = response.json()
    except ValueError as exc:
        body = response.text[:800]
        content_type = response.headers.get("content-type", "unknown")
        return (
            f"{name} 连接失败：API 返回的不是 JSON。\n\n"
            f"base url: `{base_url}`\n"
            f"model: `{payload['model']}`\n"
            f"content-type: `{content_type}`\n\n"
            f"body:\n{body}\n\n"
            f"parse error: {exc}"
        )
    content = _extract_anthropic_text(data)
    usage = data.get("usage", {})
    usage_text = f"\n\nusage: `{usage}`" if usage else ""
    return f"{name} 连接成功。模型 `{payload['model']}` 返回：\n\n{content[:500]}{usage_text}"


def _required(value: str | None, name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{name} 不能为空。请先填写 API Key。")
    return value.strip()


def normalize_dashscope_base_url(value: str | None) -> str:
    url = (value or DEFAULT_DASHSCOPE_BASE_URL).strip().rstrip("/")
    if not url:
        return DEFAULT_DASHSCOPE_BASE_URL
    if "dashscope" in url and "/compatible-mode/v1" not in url:
        host = url.split("/api/", 1)[0].split("/compatible-mode/", 1)[0].rstrip("/")
        return f"{host}/compatible-mode/v1"
    return url


def normalize_minimax_base_url(value: str | None, protocol: str | None = None) -> str:
    protocol = normalize_minimax_protocol(protocol, value)
    default = DEFAULT_MINIMAX_ANTHROPIC_BASE_URL if protocol == "anthropic" else DEFAULT_MINIMAX_OPENAI_BASE_URL
    url = (value or default).strip().rstrip("/")
    if not url:
        return default
    if "/anthropic" in url:
        return url.split("/anthropic", 1)[0].rstrip("/") + "/anthropic"
    if url == "https://api.minimax.chat/v1":
        return DEFAULT_MINIMAX_OPENAI_BASE_URL
    if url == "https://api.minimax.io":
        return DEFAULT_MINIMAX_OPENAI_BASE_URL
    if url == "https://api.minimaxi.com":
        return "https://api.minimaxi.com/v1"
    return url


def normalize_minimax_protocol(protocol: str | None, base_url: str | None = None) -> str:
    value = (protocol or "").strip().lower()
    if value in {"anthropic", "openai"}:
        return value
    if base_url and "/anthropic" in base_url:
        return "anthropic"
    return "openai"


def normalize_role_protocol(protocol: str | None, base_url: str | None = None) -> str:
    value = (protocol or "").strip().lower()
    if value in {"anthropic", "openai"}:
        return value
    if base_url and ("/anthropic" in base_url or "messages" in base_url):
        return "anthropic"
    return "openai"


def normalize_role_base_url(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def dashscope_base_url_for_region(region: str) -> str:
    return DASHSCOPE_BASE_URLS.get(region, DEFAULT_DASHSCOPE_BASE_URL)


def _http_error_hint(name: str, status_code: int, base_url: str, model: str) -> str:
    if status_code == 403 and "DashScope" in name:
        return (
            "403 排查：确认 API Key 属于这个 DashScope 地域；确认百炼/DashScope 服务已开通；"
            f"确认模型 `{model}` 在该地域可用。当前 base url: `{base_url}`。"
        )
    if status_code == 403 and name.startswith("MiniMax"):
        return (
            "403 排查：确认 MiniMax API Key 属于当前站点和协议；国际站通常用 `api.minimax.io`，中国站通常用 `api.minimaxi.com`；"
            f"确认 API Key 有模型 `{model}` 的调用权限。当前 base url: `{base_url}`。"
        )
    if status_code == 401:
        return "401 排查：API Key 无效、复制不完整，或 Authorization Bearer 不被该 endpoint 接受。"
    return "请检查 base url、模型名、账号权限、额度和服务地域。"


def _connection_error_hint(name: str, base_url: str, exc: requests.RequestException) -> str:
    detail = str(exc)
    if "WinError 10013" in detail:
        return (
            f"{name} did not reach the API: Windows blocked Python's outbound socket to `{base_url}`.\n\n"
            "This is not an API key, model, or prompt error. Allow this project's "
            "`.venv\\Scripts\\python.exe` to make outbound TCP 443 connections in Windows Defender, "
            "third-party security software, or your company network policy. Then test again.\n\n"
            f"Original network error: {detail}"
        )
    return f"{name} network connection failed before an HTTP response. base url: `{base_url}`\n\n{detail}"


def _tiny_png_base64() -> str:
    image = Image.new("RGB", (16, 16), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_anthropic_text(data: dict) -> str:
    blocks = data.get("content", [])
    texts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(texts) or str(data)[:500]
