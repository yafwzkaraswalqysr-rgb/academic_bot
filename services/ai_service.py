from __future__ import annotations
import asyncio
import logging
from config import GEMINI_API_KEY, GEMINI_MODEL
from utils.text import clean_ai_text

log = logging.getLogger(__name__)


class AIService:
    """Defensive wrapper around the official google-genai client.

    The service keeps the configured model as the primary model and has a
    small production fallback chain for model-access errors. This prevents a
    stale model name in an existing .env file from taking down every AI
    feature. Other failures are surfaced normally and are not hidden behind
    unrelated fallbacks.
    """

    FALLBACK_MODELS = (
        'gemini-3.6-flash',
        'gemini-3.5-flash',
        'gemini-3.5-flash-lite',
    )

    def __init__(self):
        self.available = False
        self.client = None
        self.active_model = GEMINI_MODEL or self.FALLBACK_MODELS[0]
        self._model_candidates = self._build_candidates(self.active_model)
        if not GEMINI_API_KEY:
            log.warning("GEMINI_API_KEY is not configured")
            return
        try:
            from google import genai
            self.client = genai.Client(api_key=GEMINI_API_KEY)
            self.available = True
            log.info("Gemini ready: %s", self.active_model)
        except Exception:
            log.exception("Gemini client initialization failed")

    @classmethod
    def _build_candidates(cls, configured: str) -> list[str]:
        out: list[str] = []
        for model in (configured, *cls.FALLBACK_MODELS):
            model = (model or '').strip()
            if model and model not in out:
                out.append(model)
        return out

    @staticmethod
    def _is_model_not_found(exc: Exception) -> bool:
        status = getattr(exc, 'status_code', None)
        code = getattr(exc, 'code', None)
        text = str(exc).lower()
        return (status == 404 or code == 404 or '404' in text) and (
            'not_found' in text or 'not found' in text or 'no longer available' in text
            or 'model' in text
        )

    @staticmethod
    def _response_text(response):
        try:
            text = getattr(response, 'text', None)
            if text and str(text).strip():
                return str(text).strip()
        except Exception:
            pass
        pieces = []
        try:
            for candidate in getattr(response, 'candidates', None) or []:
                content = getattr(candidate, 'content', None)
                for part in getattr(content, 'parts', None) or []:
                    part_text = getattr(part, 'text', None)
                    if part_text and str(part_text).strip() and not getattr(part, 'thought', False):
                        pieces.append(str(part_text).strip())
        except Exception:
            log.debug('Unable to read Gemini candidate parts', exc_info=True)
        return '\n'.join(pieces).strip() if pieces else None

    def _call_with_model(self, contents, **extra):
        """Call generate_content, switching models only for a model 404."""
        if not self.client:
            return None

        last_exc: Exception | None = None
        start_index = 0
        try:
            start_index = self._model_candidates.index(self.active_model)
        except ValueError:
            start_index = 0

        for idx in range(start_index, len(self._model_candidates)):
            model = self._model_candidates[idx]
            try:
                response = self.client.models.generate_content(
                    model=model, contents=contents, **extra
                )
                self.active_model = model
                text = self._response_text(response)
                if text:
                    return text
                log.warning('Gemini returned HTTP success but no non-thought text for model=%s; trying next model', model)
                continue
            except Exception as exc:
                last_exc = exc
                if not self._is_model_not_found(exc):
                    raise
                log.warning("Gemini model unavailable (%s); trying fallback if available", model)

        if last_exc:
            raise last_exc
        return None

    def _sync_call(self, prompt: str) -> str | None:
        if not self.client:
            return None
        try:
            text = self._call_with_model(prompt)
            return text.strip() if text else None
        except Exception:
            log.exception("Gemini request failed using available models")
            return None

    async def call_json(self, prompt: str):
        """Generate structured JSON using Gemini JSON response mode."""
        if not self.available or not self.client:
            return None
        try:
            from google.genai import types
            config = types.GenerateContentConfig(response_mime_type='application/json')
            return await asyncio.to_thread(self._sync_call_with_config, prompt, config)
        except Exception:
            log.exception('Gemini JSON request failed')
            return None

    def _sync_call_with_config(self, prompt: str, config) -> str | None:
        if not self.client:
            return None
        try:
            text = self._call_with_model(prompt, config=config)
            return text.strip() if text else None
        except Exception:
            log.exception('Gemini configured request failed using available models')
            return None

    async def call(self, prompt: str):
        if not self.available:
            return None
        try:
            return await asyncio.to_thread(self._sync_call, prompt)
        except Exception:
            log.exception("AI call wrapper failed")
            return None

    def _sync_image_call(self, image_bytes: bytes, prompt: str, mime_type: str = "image/jpeg") -> str | None:
        if not self.client or not image_bytes:
            return None
        try:
            from google.genai import types
            part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            text = self._call_with_model([part, prompt])
            return text.strip() if text else None
        except Exception:
            log.exception("Gemini image request failed using available models")
            return None

    async def analyze_image(self, image_bytes: bytes, prompt: str, mime_type: str = "image/jpeg"):
        if not self.available:
            return None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._sync_image_call, image_bytes, prompt, mime_type),
                timeout=90,
            )
        except Exception:
            log.exception("Gemini image call failed")
            return None

    def _sync_document_extract(self, path: str, mime_type: str = "application/pdf") -> str | None:
        if not self.client:
            return None
        uploaded = None
        try:
            uploaded = self.client.files.upload(file=path)
            text = self._call_with_model([uploaded, "استخرج النص الكامل من هذا المستند كما هو. لا تلخص ولا تحل الأسئلة ولا تضف معلومات. حافظ على العناوين والقوائم والجداول قدر الإمكان."])
            return text.strip() if text else None
        except Exception:
            log.exception("Gemini document extraction failed using available models")
            return None
        finally:
            try:
                if uploaded and getattr(uploaded, "name", None):
                    self.client.files.delete(name=uploaded.name)
            except Exception:
                log.debug("Could not delete temporary Gemini file", exc_info=True)

    async def extract_document(self, path: str, mime_type: str = "application/pdf"):
        if not self.available:
            return None
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._sync_document_extract, path, mime_type), timeout=120)
        except Exception:
            log.exception("Gemini document extraction wrapper failed")
            return None

    async def correct_query(self, q):
        if not self.available or not q:
            return q
        result = await self.call("صحح الأخطاء الإملائية الواضحة فقط في استعلام البحث الأكاديمي. لا تضف كلمات جديدة. أرجع النص فقط.\n" + q)
        result = (result or "").strip().strip("`").strip()
        return result[:500] if 1 <= len(result) <= 500 else q

    async def expand_query(self, q, major=None, level=None):
        if not self.available:
            return [q]
        prompt = (
            'أنت مساعد بحث أكاديمي. أرجع JSON فقط بهذا الشكل: {"queries":["..."]}. '
            f"التخصص: {major or 'غير محدد'}. المرحلة: {level or 'غير محددة'}. الاستعلام: {q}"
        )
        result = await self.call(prompt)
        if not result:
            return [q]
        from services.ai_parsing import clean_json
        data = clean_json(result)
        queries = data.get("queries") if isinstance(data, dict) else []
        if not isinstance(queries, list):
            return [q]
        out = [q]
        for item in queries:
            if isinstance(item, str) and item.strip() and item.strip() not in out:
                out.append(item.strip())
        return out[:4]

    async def answer_from_sources(self, q, content):
        if not content:
            return "لا توجد مادة كافية من المصادر للإجابة."
        result = await self.call("أجب اعتمادًا حصراً على المصادر المرفقة. لا تخترع معلومة.\nالسؤال: " + q + "\nالمصادر:\n" + content)
        return clean_ai_text(result or "تعذر توليد إجابة حاليًا.")

    async def summarize(self, text, language="ar"):
        result = await self.call("لخص النص بالعربية بدقة ودون اختلاق:\n" + text)
        return clean_ai_text(result or "")

    async def explain(self, text):
        result = await self.call("اشرح المادة للطالب بالعربية العراقية المبسطة، اعتمادًا على المادة فقط، مع أمثلة عند الحاجة دون اختلاق معلومات:\n" + text)
        return clean_ai_text(result or "")

    async def generate_flashcards(self, text):
        result = await self.call('حوّل المادة إلى بطاقات سؤال وجواب. أرجع JSON فقط: {"items":[{"question":"...","answer":"..."}]}\n' + text)
        from services.ai_parsing import clean_json
        data = clean_json(result)
        return data.get("items", []) if isinstance(data, dict) and isinstance(data.get("items"), list) else []

    async def classify_field(self, text):
        result = await self.call("حدد المجال الأكاديمي بكلمتين أو ثلاث فقط:\n" + text[:2000])
        return clean_ai_text(result or "غير محدد")

ai_service = AIService()
