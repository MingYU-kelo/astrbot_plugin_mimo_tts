import base64
import os
import traceback
import uuid

import httpx

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain, Record
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core import logger


class Main(Star):
    """Mimo TTS 语音合成插件 - 基于 MiMo V2.5 音色复刻模型"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config

        self.api_key: str = config.get("mimo_api_key", "")
        self.api_url: str = config.get("mimo_api_url", "https://api.xiaomimimo.com/v1")
        self.reference_audio_path: str = config.get("reference_audio_path", "")
        self.output_mode: str = config.get("output_mode", "llm_classify")
        if self.output_mode not in ("dual_output", "voice_only", "llm_classify"):
            logger.warning(
                f"[MimoTTS] 不支持的输出模式: {self.output_mode}，fallback 到 llm_classify"
            )
            self.output_mode = "llm_classify"
        self.text_mode: str = config.get("text_mode", "limit")
        self.text_length_limit: int = config.get("text_length_limit", 200)
        self.audio_format: str = config.get("audio_format", "wav")
        if self.audio_format not in ("wav", "pcm16"):
            logger.warning(
                f"[MimoTTS] 不支持的输出格式: {self.audio_format}，MiMo 仅支持 wav, pcm16，fallback 到 wav"
            )
            self.audio_format = "wav"
        self.request_timeout: int = config.get("request_timeout", 60)
        self.classifier_system_prompt: str = config.get(
            "classifier_system_prompt",
            "你是一个文本分类器。只回复 voice 或 text，不要回复其他内容。",
        )
        self.classifier_user_prompt: str = config.get(
            "classifier_user_prompt",
            "用户消息：{user_msg}\n\nAI 回复（节选）：{response_sample}\n\n"
            "请判断以上 AI 回复应该以「语音」还是「文字」形式输出。\n"
            "- 语音：日常闲聊、简单问候、情感交流、生活话题、轻松对话\n"
            "- 文字：专业知识问答、长文本说明、技术文档、代码、百科条目、新闻更新、复杂推理\n"
            "只回复一个词：voice 或 text",
        )

        self._reference_data_url: str | None = None
        self._cache_path: str = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "cache.txt"
        )
        self._load_reference_audio()

    def _load_reference_audio(self) -> None:
        if not self.reference_audio_path:
            logger.warning("[MimoTTS] 未配置参考音频路径")
            return
        if not os.path.exists(self.reference_audio_path):
            logger.warning(f"[MimoTTS] 参考音频文件不存在: {self.reference_audio_path}")
            return

        # MiMo 支持的输入格式
        ext = os.path.splitext(self.reference_audio_path)[1].lower()
        mimo_input_mime = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
        }
        mime_type = mimo_input_mime.get(ext)
        if not mime_type:
            logger.error(
                f"[MimoTTS] 不支持的参考音频格式: {ext}，MiMo 仅支持 wav, mp3"
            )
            return

        # 源文件的修改时间，用于缓存失效检测
        source_mtime = os.path.getmtime(self.reference_audio_path)

        # 尝试从缓存读取
        if os.path.exists(self._cache_path):
            try:
                with open(self._cache_path, encoding="utf-8") as f:
                    cache_meta = f.readline().strip()
                    cache_data_url = f.readline().strip()
                cached_path, cached_mtime = cache_meta.split("|", 1)
                if (
                    cached_path == self.reference_audio_path
                    and float(cached_mtime) == source_mtime
                    and cache_data_url.startswith("data:")
                ):
                    self._reference_data_url = cache_data_url
                    logger.info(
                        f"[MimoTTS] 从缓存加载参考音频: {self._cache_path}"
                    )
                    return
                logger.info("[MimoTTS] 缓存已过期，重新编码...")
            except (ValueError, OSError) as e:
                logger.warning(f"[MimoTTS] 缓存读取失败，重新编码: {e}")

        # 编码并写入缓存
        try:
            with open(self.reference_audio_path, "rb") as f:
                audio_bytes = f.read()
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            data_url = f"data:{mime_type};base64,{audio_b64}"
            self._reference_data_url = data_url

            with open(self._cache_path, "w", encoding="utf-8") as f:
                f.write(f"{self.reference_audio_path}|{source_mtime}\n")
                f.write(data_url)
            logger.info(
                f"[MimoTTS] 参考音频已编码并缓存: {self.reference_audio_path} "
                f"(ext={ext}, mime={mime_type}, {len(audio_bytes)} bytes) → {self._cache_path}"
            )
        except Exception as e:
            logger.error(f"[MimoTTS] 参考音频编码失败: {e}")

    @filter.on_decorating_result()
    async def tts_decorate(self, event: AstrMessageEvent) -> None:
        """根据 output_mode 将文本转为语音"""
        if not self._reference_data_url:
            logger.warning("[MimoTTS] 参考音频未就绪，跳过 TTS")
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        if self.output_mode == "dual_output":
            await self._do_dual_output(result)
        elif self.output_mode == "voice_only":
            await self._do_voice_only(result)
        else:
            # llm_classify: LLM 分类器决定
            response_text = self._extract_text(result)
            if not response_text:
                return
            use_voice = await self._classify_response(event, response_text)
            if use_voice is None:
                logger.warning("[MimoTTS] 分类失败，fallback 文字输出")
                return
            if use_voice:
                logger.info(f"[MimoTTS] LLM 分类: 语音输出 ({response_text[:40]}...)")
                await self._do_voice_only(result)
            else:
                logger.info(f"[MimoTTS] LLM 分类: 文字输出 ({response_text[:40]}...)")

    def _extract_text(self, result) -> str:
        texts = []
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                texts.append(comp.text.strip())
        return " ".join(texts)

    async def _classify_response(
        self, event: AstrMessageEvent, response_text: str
    ) -> bool | None:
        """用 LLM 对回复文本做分类：适合语音还是文字输出"""
        sample = response_text[:500]
        user_msg = event.message_str[:200] if event.message_str else ""

        user_prompt = self.classifier_user_prompt.format(
            user_msg=user_msg,
            response_sample=sample,
        )

        try:
            provider_id = await self.context.get_current_chat_provider_id(
                event.unified_msg_origin
            )
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=user_prompt,
                system_prompt=self.classifier_system_prompt,
                max_tokens=10,
                temperature=0.1,
            )
            answer = llm_resp.completion_text.strip().lower()
            logger.info(f"[MimoTTS] 分类器原始回复: {answer}")
            if "voice" in answer:
                return True
            if "text" in answer:
                return False
            logger.warning(f"[MimoTTS] 分类器返回异常: {answer}")
            return None
        except Exception as e:
            logger.error(f"[MimoTTS] 分类 LLM 调用失败: {e}")
            return None

    async def _do_dual_output(self, result) -> None:
        new_chain = []
        segment_index = 0
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text and len(comp.text.strip()) > 1:
                text = comp.text.strip()
                segment_index += 1
                if not self._should_convert(text, segment_index):
                    new_chain.append(comp)
                    continue
                try:
                    audio_bytes = await self._call_mimo_tts(text)
                    if audio_bytes:
                        audio_path = self._save_audio(audio_bytes)
                        record = Record(file=audio_path, url=audio_path, text=text)
                        new_chain.append(record)
                        new_chain.append(comp)
                        logger.info(f"[MimoTTS] 双输出 TTS 成功: {text[:50]}...")
                    else:
                        new_chain.append(comp)
                except Exception:
                    logger.error(f"[MimoTTS] TTS 失败:\n{traceback.format_exc()}")
                    new_chain.append(comp)
            else:
                new_chain.append(comp)
        result.chain = new_chain

    async def _do_voice_only(self, result) -> None:
        segments: list = []
        segment_index = 0
        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text and len(comp.text.strip()) > 1:
                text = comp.text.strip()
                segment_index += 1
                if self._should_convert(text, segment_index):
                    segments.append(text)
            elif not isinstance(comp, Plain):
                segments.append(comp)

        new_chain = []
        for seg in segments:
            if isinstance(seg, str):
                try:
                    audio_bytes = await self._call_mimo_tts(seg)
                    if audio_bytes:
                        audio_path = self._save_audio(audio_bytes)
                        record = Record(file=audio_path, url=audio_path, text=seg)
                        new_chain.append(record)
                        logger.info(f"[MimoTTS] 仅语音 TTS 成功: {seg[:50]}...")
                except Exception:
                    logger.error(f"[MimoTTS] 仅语音 TTS 失败，fallback 文字:\n{traceback.format_exc()}")
                    new_chain.append(Plain(seg))
            else:
                new_chain.append(seg)
        result.chain = new_chain

    def _should_convert(self, text: str, index: int) -> bool:
        if self.text_mode == "first_only" and index > 1:
            return False
        if self.text_mode == "limit" and len(text) > self.text_length_limit:
            return False
        return True

    async def _call_mimo_tts(self, text: str) -> bytes | None:
        body = {
            "model": "mimo-v2.5-tts-voiceclone",
            "messages": [{"role": "assistant", "content": text}],
            "audio": {
                "voice": self._reference_data_url,
                "format": self.audio_format,
            },
            "modalities": ["text", "audio"],
        }
        async with httpx.AsyncClient(timeout=self.request_timeout) as client:
            resp = await client.post(
                f"{self.api_url}/chat/completions",
                headers={
                    "api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        audio_data = data.get("choices", [{}])[0].get("message", {}).get("audio", {})
        audio_b64 = audio_data.get("data", "")
        if not audio_b64:
            logger.error("[MimoTTS] API 响应中无音频数据")
            return None
        return base64.b64decode(audio_b64)

    # MiMo 输出格式 → 文件扩展名
    _OUTPUT_EXT_MAP = {"wav": ".wav", "pcm16": ".pcm"}

    def _save_audio(self, audio_bytes: bytes) -> str:
        temp_dir = os.path.join(os.path.dirname(self.reference_audio_path), "tts_output")
        os.makedirs(temp_dir, exist_ok=True)
        ext = self._OUTPUT_EXT_MAP.get(self.audio_format, f".{self.audio_format}")
        filename = f"tts_{uuid.uuid4().hex[:8]}{ext}"
        filepath = os.path.join(temp_dir, filename)
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        logger.info(f"[MimoTTS] 音频已保存: {filepath} ({len(audio_bytes)} bytes)")
        return filepath
