from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import discord

from .lang_codes import ISO639_MAP


@dataclass(slots=True)
class GoogleTranslateResponse:
    data: dict

    @classmethod
    def from_json(cls, data: dict) -> GoogleTranslateResponse:
        return cls(data=data["data"])


@dataclass(slots=True)
class DetectedLanguage:
    language: str
    isReliable: bool
    confidence: float

    def __str__(self):
        return self.language

    @classmethod
    def from_json(cls, data: List[dict]) -> DetectedLanguage:
        return cls(**data[0])


@dataclass(slots=True)
class DetectLanguageResponse(GoogleTranslateResponse):
    detections: List[DetectedLanguage]

    @classmethod
    def from_json(cls, data: dict) -> DetectLanguageResponse:
        return cls(
            data=data["data"],
            detections=[DetectedLanguage.from_json(i) for i in data["data"]["detections"]],
        )

    @property
    def language(self) -> Optional[DetectedLanguage]:
        conf = 0.0
        ret = None
        for lang in self.detections:
            if lang.confidence > conf:
                ret = lang
                conf = lang.confidence
        return ret


@dataclass(slots=True)
class TranslateTextResponse(GoogleTranslateResponse):
    translations: List[Translation]

    def __str__(self):
        return str(self.translations[0])

    @classmethod
    def from_json(cls, data: dict) -> TranslateTextResponse:
        return cls(
            data=data["data"],
            translations=[Translation.from_json(i) for i in data["data"]["translations"]],
        )

    def embed(
        self,
        author: discord.Member | discord.User,
        from_language: str,
        to_language: str,
        requestor: Optional[discord.Member | discord.User] = None,
    ) -> Tuple[str, discord.Embed]:
        em = discord.Embed(colour=0x5191F5, description=str(self.translations[0]))
        em.set_author(
            name="Google Translate",
            icon_url="https://cdn.discordapp.com/emojis/914867101360087041.png",
        )

        # strip dumb -Latn suffixes
        from_language = from_language.replace("-Latn", "")
        to_language = to_language.replace("-Latn", "")

        from_ln = ISO639_MAP.get(from_language.lower()) or from_language.upper()
        to_ln = ISO639_MAP.get(to_language.lower()) or to_language.upper()
        detail_string = requestor.mention if requestor else author.mention
        em.set_footer(text=f"{from_ln}  →  {to_ln}")
        return f"> Requested by: {detail_string}", em


@dataclass(slots=True)
class Translation:
    detected_source_language: Optional[str]
    model: Optional[str]
    translated_text: str

    def __str__(self):
        return self.translated_text

    @classmethod
    def from_json(cls, data: dict) -> Translation:
        return cls(
            detected_source_language=data.get("detectedSourceLanguage"),
            model=data.get("model"),
            translated_text=data["translatedText"],
        )

    @property
    def text(self) -> str:
        return self.translated_text