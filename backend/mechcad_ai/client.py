from __future__ import annotations

"""OpenAI / Anthropic compatible HTTP client, extracted from the legacy Gradio MVP.

Supports two wire protocols:
- openai:    POST {base_url}/chat/completions  (Authorization: Bearer)
- anthropic: POST {base_url}/v1/messages        (X-Api-Key + anthropic-version)

Both retry a trailing `/v1` path when the configured base url does not already
end with it, because several Chinese providers expose both forms.
"""

import json
import os
from typing import Any

import requests

DEFAULT_TIMEOUT = 90

# Role-specific env vars, all optional. They mirror backend.schemas.ModelConfig.
ENV_ROLE_MAP = {
    "vision": {
        "api_key": "MECHCAD_VISION_API_KEY",
        "base_url": "MECHCAD_VISION_BASE_URL",
        "model": "MECHCAD_VISION_MODEL",
        "protocol": "MECHCAD_VISION_PROTOCOL",
    },
    "planner": {
        "api_key": "MECHCAD_PLANNER_API_KEY",
        "base_url": "MECHCAD_PLANNER_BASE_URL",
        "model": "MECHCAD_PLANNER_MODEL",
        "protocol": "MECHCAD_PLANNER_PROTOCOL",
    },
}


class ApiCallError(RuntimeError):
    """Raised when the external model call fails at the HTTP/parse level."""


def resolve_role_config(settings, role: str) -> dict[str, str]:
    """Resolve api_key/base_url/model/protocol for a role from ModelConfig then env."""
    env = ENV_ROLE_MAP[role]
    protocol = str(getattr(settings, f"{role}_protocol", "") or "").strip().lower()
    base_url = str(getattr(settings, f"{role}_base_url", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, f"{role}_api_key", "") or "").strip()
    model = str(getattr(settings, f"{role}_model", "") or "").strip()

    if not api_key:
        api_key = os.getenv(env["api_key"], "").strip()
    if not base_url:
        base_url = os.getenv(env["base_url"], "").strip().rstrip("/")
    if not model:
        model = os.getenv(env["model"], "").strip()
    if not protocol:
        protocol = os.getenv(env["protocol"], "").strip().lower()

    if not protocol:
        protocol = "anthropic" if "/anthropic" in base_url else "openai"
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "protocol": protocol,
    }


def has_configured_model(settings, role: str) -> bool:
    config = resolve_role_config(settings, role)
    return bool(config["api_key"] and config["base_url"] and config["model"])


def test_model_connection(settings, role: str) -> dict[str, Any]:
    """Make a minimal text-only request and return safe diagnostics for the UI."""
    if role not in ENV_ROLE_MAP:
        raise ValueError(f"unsupported model role: {role}")
    config = resolve_role_config(settings, role)
    used_env_fallback = any(
        not str(getattr(settings, f"{role}_{key}", "") or "").strip()
        for key in ("api_key", "base_url", "model", "protocol")
    )
    if not config["api_key"] or not config["base_url"] or not config["model"]:
        return {
            "ok": False,
            "message": "配置不完整：请填写 API Key、Base URL 和模型名称，或确认 .env 已配置。",
            "endpoint": None,
            "status_code": None,
            "content_type": None,
            "used_env_fallback": used_env_fallback,
        }
    messages = [{"role": "user", "content": "只回复 OK，不要输出其他内容。"}]
    if config["protocol"] == "anthropic":
        urls = [f"{config['base_url']}/v1/messages"]
    else:
        urls = [f"{config['base_url']}/chat/completions"]
        if not config["base_url"].endswith("/v1"):
            urls.append(f"{config['base_url']}/v1/chat/completions")
    last: dict[str, Any] = {}
    for url in urls:
        try:
            if config["protocol"] == "anthropic":
                response = _post_anthropic_test(config, url, messages)
            else:
                response = _post_openai_test(config, url, messages)
        except requests.RequestException as exc:
            last = {"message": f"网络不可达：{exc}", "endpoint": url}
            continue
        content_type = response.headers.get("content-type", "")
        last = {
            "endpoint": url,
            "status_code": response.status_code,
            "content_type": content_type,
        }
        if response.ok:
            if "json" not in content_type.lower():
                last["message"] = "服务返回成功但不是 JSON，Base URL 可能指向网页地址。"
                continue
            try:
                data = response.json()
                if (config["protocol"] == "anthropic" and data.get("content")) or (
                    config["protocol"] != "anthropic" and data.get("choices")
                ):
                    last["ok"] = True
                    last["message"] = "连接成功：最小文本请求已返回。"
                    return last | {"used_env_fallback": used_env_fallback}
                last["message"] = "返回 JSON 但缺少标准模型响应字段。"
            except ValueError:
                last["message"] = "服务返回内容无法解析为 JSON。"
        else:
            if response.status_code in (401, 403):
                last["message"] = f"认证失败（HTTP {response.status_code}）：请检查 API Key、权限和模型访问范围。"
            elif response.status_code == 404:
                last["message"] = "接口不存在：请检查协议与 Base URL，OpenAI 兼容应指向 API 根路径。"
            else:
                last["message"] = f"服务请求失败（HTTP {response.status_code}）。"
        if "text/html" in content_type.lower():
            last["message"] += " 返回的是 HTML，Base URL 很可能填成了网站首页。"
    return {"ok": False, **last, "used_env_fallback": used_env_fallback}


def _post_openai_test(config: dict[str, str], url: str, messages: list[dict[str, Any]]):
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        json={"model": config["model"], "messages": messages, "max_tokens": 8, "temperature": 0},
        timeout=20,
    )


def _post_anthropic_test(config: dict[str, str], url: str, messages: list[dict[str, Any]]):
    return requests.post(
        url,
        headers={
            "X-Api-Key": config["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"model": config["model"], "messages": messages, "max_tokens": 8, "temperature": 0},
        timeout=20,
    )


def chat_completion(
    settings,
    role: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    timeout: int = DEFAULT_TIMEOUT,
    response_json: bool = False,
    image_base64: str | None = None,
    image_media_type: str = "image/png",
) -> str:
    """Send a chat request to the configured model for a role and return the text.

    For vision roles an image (raw base64, no data-url prefix) may be attached to
    the final user message when ``image_base64`` is given.
    """
    config = resolve_role_config(settings, role)
    if not config["api_key"]:
        raise ApiCallError(f"{role} API key is not configured")
    if config["protocol"] == "anthropic":
        return _anthropic_messages(config, messages, max_tokens, temperature, timeout, image_base64, image_media_type)
    return _openai_chat(config, messages, max_tokens, temperature, timeout, response_json, image_base64, image_media_type)


def _openai_chat(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    response_json: bool,
    image_base64: str | None,
    image_media_type: str,
) -> str:
    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    if image_base64:
        _attach_openai_image(payload["messages"], image_base64, image_media_type)

    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    urls = [f"{config['base_url']}/chat/completions"]
    if not config["base_url"].rstrip("/").endswith("/v1"):
        urls.append(f"{config['base_url']}/v1/chat/completions")

    last_error: Exception | None = None
    for url in urls:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            continue
        content_type = response.headers.get("content-type", "").lower()
        if response.ok and "json" in content_type:
            data = response.json()
            return _extract_openai_text(data)
        if response.ok and "text/html" in content_type:
            last_error = ApiCallError(f"provider returned HTML (base url may be missing /v1): {url}")
            continue
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}")
    if last_error:
        raise ApiCallError(f"model call failed: {last_error}")
    raise ApiCallError("model call failed: no response")


def _anthropic_messages(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    image_base64: str | None,
    image_media_type: str,
) -> str:
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            system_parts.append(str(content))
        else:
            chat_messages.append({"role": role, "content": str(content)})
    if image_base64:
        chat_messages[-1]["content"] = [
            {"type": "text", "text": chat_messages[-1]["content"]},
            {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_base64}},
        ]

    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": chat_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    url = f"{config['base_url']}/v1/messages"
    headers = {
        "X-Api-Key": config["api_key"],
        "Authorization": f"Bearer {config['api_key']}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ApiCallError(f"model call failed: {exc}") from exc
    if not response.ok:
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}")
    return _extract_anthropic_text(response.json())


def _attach_openai_image(messages: list[dict[str, Any]], image_base64: str, media_type: str) -> None:
    data_url = f"data:{media_type};base64,{image_base64}"
    last = messages[-1]
    if isinstance(last.get("content"), str):
        last["content"] = [
            {"type": "text", "text": last["content"]},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    elif isinstance(last.get("content"), list):
        last["content"].append({"type": "image_url", "image_url": {"url": data_url}})


def _extract_openai_text(data: dict[str, Any]) -> str:
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ApiCallError(f"unexpected OpenAI-compatible response: {json.dumps(data, ensure_ascii=False)[:300]}") from exc


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    blocks = data.get("content", [])
    texts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
    if texts:
        return "\n".join(texts)
    raise ApiCallError(f"unexpected Anthropic-compatible response: {json.dumps(data, ensure_ascii=False)[:300]}")


def strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def parse_json_object(content: str) -> dict[str, Any]:
    text = strip_json_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a leading "```json" block or surrounding prose by extracting the
        # first balanced {...} region.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ApiCallError("model returned valid JSON but not an object")
    return parsed
