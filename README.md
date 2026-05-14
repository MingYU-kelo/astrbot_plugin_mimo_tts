# Mimo TTS 语音合成插件

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.0-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow)](https://www.python.org/)

基于 **小米 MiMo V2.5 音色复刻模型**（`mimo-v2.5-tts-voiceclone`），将 AstrBot 的 AI 文本回复实时转换为语音消息发送。

## 特性

- 音色复刻 - 使用 MiMo 语音克隆模型，仅需 5 秒参考音频即可复刻任意音色
- LLM 智能分类 - 由 AI 自动判断回复适合语音还是文字（闲聊语音，专业问答文字）
- 三档输出模式 - 双输出、纯语音、LLM 分类器，按需切换
- 缓存加速 - 参考音频 Base64 编码后缓存至本地，重启/重载秒级加载
- 多格式支持 - 输入支持 `wav` / `mp3`，输出支持 `wav` / `pcm16`
- 分段控制 - 可配置文本长度阈值，超长内容自动跳过 TTS

## 工作原理

```
用户消息
  → AstrBot Agent 生成 AI 回复
  → 插件 on_decorating_result 拦截
  → 根据 output_mode 决策：
      ├─ dual_output   → 文字 + 语音一起发
      ├─ voice_only    → 仅发语音，替换文字
      └─ llm_classify  → 调用分类器：
          回复文本 + 用户消息 → LLM 判断 → voice / text
  → 调用 MiMo TTS API 生成音频
  → 保存为 wav/pcm16，发送语音消息
```

## 安装

### 方式一：WebUI 安装

1. AstrBot WebUI → 插件管理 → 右上角 `+` → 输入仓库地址
2. 粘贴：`https://github.com/MingYU-kelo/astrbot_plugin_mimo_tts`
3. 点击安装，等待完成

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/MingYU-kelo/astrbot_plugin_mimo_tts
```

然后重启 AstrBot 或在 WebUI 中重载插件。

## 配置

所有配置项均可在 WebUI 插件配置面板中修改。

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `mimo_api_key` | string | — | MiMo API Key，从 [MiMo 开放平台](https://platform.xiaomimimo.com) 获取 |
| `mimo_api_url` | string | `https://api.xiaomimimo.com/v1` | API 地址。Token Plan 用户需改为专属地址 |
| `reference_audio_path` | string | — | 参考音频文件**绝对路径**，推荐使用mp3格式，用于音色复刻（5~30 秒最佳） |
| `audio_format` | string | `wav` | 输出音频格式：`wav` 兼容性最好，`pcm16` 适合流式 |
| `request_timeout` | int | `60` | API 请求超时秒数，建议 30~120 |

### 输出模式

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `output_mode` | string | `llm_classify` | 见下方[输出模式详解](#输出模式详解) |
| `text_mode` | string | `limit` | `limit`=超长跳过 / `all`=全转 / `first_only`=仅首段 |
| `text_length_limit` | int | `500` | `limit` 模式下跳过 TTS 的字数阈值 |

### 输出模式详解

| 模式 | 行为 |
|------|------|
| `llm_classify` | LLM 分析回复内容，**闲聊/问候/情感** 语音，**技术/百科/长文** 文字 |
| `dual_output` | 文字和语音**同时发送**，用户即能看也能听 |
| `voice_only` | **仅发送语音**，不显示文字 |

> 分类器在 `llm_classify` 模式下工作，消耗极少量 token（`max_tokens=10, temperature=0.1`）。

### 分类器提示词（llm_classify 模式专用）

| 配置项 | 说明 |
|--------|------|
| `classifier_system_prompt` | 分类器角色设定，默认：`"你是一个文本分类器。只回复 voice 或 text，不要回复其他内容。"` |
| `classifier_user_prompt` | 分类模板，支持 `{user_msg}` `{response_sample}` 占位符 |

## 音色复刻指南

### 准备参考音频

1. 录制或准备一段 **5~30 秒** 的清晰人声音频
2. 格式：`wav` 或 `mp3`
3. 要求：单人声、背景安静、吐字清晰
4. 将文件路径填入 `reference_audio_path` 配置项

### 测试音色

配置完成后，通过 QQ 向 Bot 发送一条简短消息（如"你好"），Bot 将以克隆音色回复语音。

## 支持的平台

| 平台 | 支持 |
|------|------|
| QQ (aiocqhttp / OneBot v11) | 支持 |
| Telegram | 未测试 |
| Discord | 未测试 |
| 企业微信 | 未测试 |

## 依赖

- `httpx >= 0.27.0`
- AstrBot >= 4.0
- MiMo API Key（[免费注册](https://platform.xiaomimimo.com)）

## 许可证

MIT © 2026 MingYU-kelo
