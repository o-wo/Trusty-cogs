from __future__ import annotations

import logging
import time
from collections import OrderedDict
from copy import deepcopy
from io import StringIO
from typing import Any, Dict, Mapping, Optional, Tuple, Union, cast

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pprint

from .errors import GoogleTranslateAPIError
from .flags import FLAGS
from .lang_codes import ISO639_MAP
from .models import DetectedLanguage, DetectLanguageResponse, TranslateTextResponse

BASE_URL = "https://translation.googleapis.com"
LOGGER = logging.getLogger("red.google.translate.api")


class FixedSizeOrderedDict(OrderedDict):
    # https://stackoverflow.com/a/49274421
    def __init__(self, *args: Any, max_len: int = 0, **kwargs: Any):
        self._max_len = max_len
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: int, value: Any):
        super().__setitem__(key, value)
        if self._max_len > 0:
            if len(self) > self._max_len:
                self.popitem(False)


class GoogleTranslator:
    def __init__(
        self,
        api_token: Optional[str],
        session: Optional[aiohttp.ClientSession] = None,
        *,
        stats_counter: StatsCounter,
    ):
        self._api_token = api_token
        self.session = session or aiohttp.ClientSession(
            headers={"User-Agent": "Trusty-cogs Translate cog for Red-DiscordBot"}
        )
        self.stats_counter = stats_counter
        self._cache_limit = 128
        self._translation_cache = FixedSizeOrderedDict(max_len=self._cache_limit)
        self._detection_cache = FixedSizeOrderedDict(max_len=self._cache_limit)

    @property
    def has_token(self):
        return self._api_token is not None

    async def close(self):
        await self.stats_counter.save()
        await self.session.close()

    async def detect_language(
        self,
        text: str,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> Optional[DetectedLanguage]:
        """
        Detect the language from given text
        """
        if not self._api_token:
            raise GoogleTranslateAPIError("The API token is missing.")
        # Hash the text for a relatively unique key
        # I am not concerned about collisions here just memory
        # a user message can be up to 4000 characters long which would be 4049 bytes
        # a hash is only 36 bytes and since we're caching the result which is much larger
        # there's no reason to cache the original text just a hash of it
        cache_key = hash(text)
        if cache_key in self._detection_cache:
            return self._detection_cache[cache_key]
        params = {"q": text, "key": self._api_token}
        url = f"{BASE_URL}/language/translate/v2/detect"
        data = {}
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    LOGGER.warning("GET %s returned code: %s", url, resp.status)
                    return None
                data = await resp.json()
        except Exception as exc:
            LOGGER.warning("Error while requesting language detection", exc_info=exc)
            return None
        LOGGER.debug("DetectLanguageResponse:\n %s", pprint(data))
        if "error" in data:
            LOGGER.warning(data["error"]["message"])
            raise GoogleTranslateAPIError(data["error"]["message"])
        detection = DetectLanguageResponse.from_json(data)
        await self.stats_counter.add_detect(guild)
        self._detection_cache[cache_key] = detection.language
        return detection.language

    async def translate_text(
        self,
        target: str,
        text: str,
        from_lang: Optional[str] = None,
        *,
        guild: Optional[discord.Guild] = None,
    ) -> Optional[TranslateTextResponse]:
        """
        request to translate the text
        """
        if not self._api_token:
            raise GoogleTranslateAPIError("The API token is missing.")
        # Hash the text for a relatively unique key
        # I am not concerned about collisions here just memory
        # a user message can be up to 4000 characters long which would be 4049 bytes
        # a hash is only 36 bytes and since we're caching the result which is much larger
        # there's no reason to cache the original text just a hash of it
        cache_key = (target, hash(text), from_lang)
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]
        formatting = "text"
        params = {
            "q": text,
            "target": target,
            "key": self._api_token,
            "format": formatting,
        }
        if from_lang is not None:
            params["source"] = from_lang.replace("-Latn", "")
        url = f"{BASE_URL}/language/translate/v2"
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    LOGGER.warning("GET %s returned code: %s", url, resp.status)
                    return None
                data = await resp.json()
        except Exception as exc:
            LOGGER.warning("Error in querying translation from API", exc_info=exc)
            return None
        LOGGER.debug("TranslateTextResponse:\n %s", pprint(data))
        if "error" in data:
            LOGGER.error(data["error"]["message"])
            raise GoogleTranslateAPIError(data["error"]["message"])
        translation = TranslateTextResponse.from_json(data)
        await self.stats_counter.add_requests(guild, text)
        self._translation_cache[cache_key] = translation
        return translation


class StatsCounter:
    def __init__(self, config: Config):
        self.config = config
        self._guild_counter: Dict[int, Dict[str, int]] = {}
        self._global_counter: Dict[str, int] = {}

    async def text(self, guild: Optional[discord.Guild] = None) -> str:
        tr_keys = {
            "requests": "API Requests:",
            "detect": "Language Detect calls:",
            "characters": "Characters requested:",
        }
        gl_count = self._global_counter or await self.config.count()
        msg = StringIO()
        msg.write("### **Global Usage**:\n")
        for key, value in gl_count.items():
            msg.write(f"- {tr_keys[key]}  **{value}**\n")
        if guild is not None:
            count = (
                self._guild_counter[guild.id]
                if guild.id in self._guild_counter
                else await self.config.guild(guild).count()
            )
            msg.write(f"### **{guild}'s Usage**:\n")
            for key, value in count.items():
                msg.write(f"- {tr_keys[key]}  **{value}**\n")
        out = msg.getvalue()
        msg.close()
        return out

    async def initialize(self):
        self._global_counter = await self.config.count()
        all_guilds = await self.config.all_guilds()
        for g_id, data in all_guilds.items():
            self._guild_counter[g_id] = data["count"]

    async def save(self):
        async with self.config.count() as count:
            count = cast(dict, count)
            for key, value in self._global_counter.items():
                count[key] = value
        for guild_id, data in self._guild_counter.items():
            async with self.config.guild_from_id(int(guild_id)).count() as count:
                count = cast(dict, count)
                for key, value in data.items():
                    count[key] = value

    async def add_detect(self, guild: Optional[discord.Guild]):
        if guild:
            LOGGER.debug("+1 detect counter for guild: %s (ID: %s)", guild.name, guild.id)
            if guild.id not in self._guild_counter:
                self._guild_counter[guild.id] = await self.config.guild(guild).count()
            self._guild_counter[guild.id]["detect"] += 1
        if not self._global_counter:
            self._global_counter = await self.config.count()
        self._global_counter["detect"] += 1

    async def add_requests(self, guild: Optional[discord.Guild], message: str):
        if guild:
            LOGGER.debug("+1 requests counter for guild: %s (ID: %s)", guild.name, guild.id)
            if guild.id not in self._guild_counter:
                self._guild_counter[guild.id] = await self.config.guild(guild).count()
            self._guild_counter[guild.id]["requests"] += 1
            self._guild_counter[guild.id]["characters"] += len(message)
        if not self._global_counter:
            self._global_counter = await self.config.count()
        self._global_counter["requests"] += 1
        self._global_counter["characters"] += len(message)


class GoogleTranslateAPI:
    config: Config
    bot: Red
    cache: dict
    _key: Optional[str]
    _tr: GoogleTranslator

    async def translate_from_message(
        self, inter: discord.Interaction, message: discord.Message
    ) -> None:
        LOGGER.debug(
            "%s used context menu command in #%s (%s)",
            str(inter.user), str(inter.channel), str(inter.guild)
        )
        if not self._tr.has_token:
            await inter.response.send_message(
                "The bot owner needs to set an API key first!", ephemeral=True
            )
            return
        await inter.response.defer(ephemeral=True)
        to_translate = ""
        if message.embeds:
            if message.embeds[0].description:
                to_translate = message.embeds[0].description
        else:
            to_translate = message.clean_content

        if not to_translate:
            await inter.followup.send(
                "Meow! Google said: nothing worthy to translate in that message! 🤓",
                ephemeral=True,
            )
            return
        target = str(inter.locale).split("-")[0]
        LOGGER.debug(
            "User locale was %s (%s)\n""Message contents:\n %s",
            inter.locale.name, target, to_translate,
        )
        try:
            detected_lang = await self._tr.detect_language(to_translate, guild=inter.guild)
        except GoogleTranslateAPIError as exc:
            LOGGER.warning("Error while translating", exc_info=exc)
            await inter.followup.send(f"Something went wrong: {exc}", ephemeral=True)
            return
        except Exception as err:
            LOGGER.warning("Error detecting language", exc_info=err)
            await inter.followup.send(f"Something went wrong: {err}")
            return
        if detected_lang is None:
            await inter.followup.send(
                "Could not auto-detect the language to translate to!", ephemeral=True
            )
            return
        from_lang = detected_lang.language
        to_lang = target
        if from_lang == to_lang:
            from_ln = ISO639_MAP.get(from_lang) or from_lang.upper()
            to_ln = ISO639_MAP.get(to_lang) or to_lang.upper()
            # don't post anything if the detected language is the same
            await inter.followup.send(
                f"Could not translate `{from_ln}` to `{to_ln}`. Same language!?",
                ephemeral=True,
            )
            return

        try:
            translated_text = await self._tr.translate_text(
                target, to_translate, from_lang, guild=inter.guild
            )
        except Exception as err:
            LOGGER.error(
                "Error translating message in Guild={%s} Channel={%s}",
                inter.guild_id, inter.channel_id, exc_info=err
            )
            await inter.followup.send(f"Something went wrong: {err}")
            return
        if not translated_text:
            await inter.followup.send(
                "Meow! no text content found to translate in that message!", ephemeral=True
            )
            return
        # translation = (translated_text, from_lang, to_lang)
        _, embed = translated_text.embed(message.author, from_lang, to_lang, inter.user)
        await inter.followup.send(embed=embed, ephemeral=True)
        return

    @tasks.loop(seconds=120)
    async def translation_loop(self):
        self.cache["translations"] = []
        await self._tr.stats_counter.save()
        return

    async def check_bw_list(
        self,
        guild: discord.Guild,
        channel: discord.abc.GuildChannel | discord.Thread,
        member: Union[discord.Member, discord.User],
    ) -> bool:
        can_run = True
        if guild.id not in self.cache["guild_blacklist"]:
            self.cache["guild_blacklist"][guild.id] = await self.config.guild(guild).blacklist()
        if guild.id not in self.cache["guild_whitelist"]:
            self.cache["guild_whitelist"][guild.id] = await self.config.guild(guild).whitelist()
        whitelist = self.cache["guild_whitelist"][guild.id]
        blacklist = self.cache["guild_blacklist"][guild.id]
        if whitelist:
            #  LOGGER.debug('entering whitelist check...')
            can_run = False
            if channel.id in whitelist:
                can_run = True
            if channel.category_id and channel.category_id in whitelist:
                can_run = True
            if member.id in whitelist:
                can_run = True
            for role in getattr(member, "roles", []):
                if role.is_default():
                    continue
                if role.id in whitelist:
                    can_run = True
            return can_run
        else:
            if channel.id in blacklist:
                can_run = False
            if channel.category_id and channel.category_id in blacklist:
                can_run = False
            if member.id in blacklist:
                LOGGER.debug("%s (%s) is in blacklist so abort", member.id, str(member))
                can_run = False
            for role in getattr(member, "roles", []):
                role = cast(discord.Role, role)
                if role.is_default():
                    continue
                if role.id in blacklist:
                    LOGGER.debug("Role=%s (%s) is in guild blacklist so abort", role.id, str(role))
                    can_run = False
        return can_run

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """
        Translates the message based off reactions with country flags
        """
        if str(payload.emoji) not in FLAGS:
            #  LOGGER.debug("Emoji is not in the flags")
            return
        if not self._tr.has_token:
            LOGGER.debug("API key not set? huhhhhh?")
            return
        if not payload.guild_id: # ignore DMs /shrug
            LOGGER.debug("reaction was in my DMs with User=%s", payload.user_id)
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            LOGGER.debug("cannot find Guild by ID: %s", payload.guild_id)
            return
        if payload.emoji.is_custom_emoji():
            return
        if payload.message_id in self.cache["translations"]:
            return
        channel = cast(
            Optional[discord.TextChannel | discord.Thread],
            guild.get_channel_or_thread(payload.channel_id)
        )
        if not channel:
            LOGGER.debug("channel=%s not found", payload.channel_id)
            return
        if not channel.permissions_for(channel.guild.me).send_messages:
            LOGGER.debug(
                "User=%s added reaction %s in #%s\n %s",
                payload.user_id, str(payload.emoji), str(channel), payload.jump_url
            )
            LOGGER.debug("But I cannot send_messages in this channel so ABORTED!")
            return
        if await self.bot.cog_disabled_in_guild(self, guild):  # type: ignore
            return
        if not await self.config.guild(guild).reaction():
            LOGGER.debug(
                "%s (%s) server has not opted for reaction translations",
                str(guild), guild.id
            )
            return
        if not await self._check_cooldown(
            payload.message_id, FLAGS[str(payload.emoji)]["code"]
        ):
            LOGGER.debug("This message has hit the cooldown checks")
            return
        #  async with channel.typing():
        reacted_user = payload.member or guild.get_member(payload.user_id)
        if not reacted_user:
            LOGGER.debug("User=%s is not in guild?", payload.user_id)
            return

        if reacted_user.bot:  # ignore reactions added by bots
            return
        if not await self.check_bw_list(guild, channel, reacted_user):
            LOGGER.debug("self.check_bw_list check did not pass, aborted!")
            return
        if guild.id not in self.cache["guild_reactions"]:
            self.cache["guild_reactions"].append(guild.id)
        LOGGER.debug(
            "%s (%s) added reaction (%s) in #%s\n %s",
            str(reacted_user),
            payload.user_id,
            str(payload.emoji),
            str(channel),
            payload.jump_url,
        )
        message = self.bot._connection._get_message(payload.message_id)
        if not message:
            try:
                message = await channel.fetch_message(payload.message_id)
            except (discord.NotFound, discord.HTTPException) as exc:
                LOGGER.debug("the target message could not be found!", exc_info=exc)
                return
        LOGGER.debug("target message:\n %s", message.content)
        if message.id not in self.cache["cooldown_translations"]:
            if not self.cache["cooldown"]:
                self.cache["cooldown"] = await self.config.cooldown()
            cooldown = deepcopy(self.cache["cooldown"])
        else:
            cooldown = self.cache["cooldown_translations"][message.id]
        cooldown["wait"] = time.time() + cooldown["timeout"]
        cooldown["past_flags"].append(str(payload.emoji))
        self.cache["cooldown_translations"][message.id] = cooldown

        translated_output = await self.translate_message(
            message, to_lang=None, flag=str(payload.emoji), reacted_user=reacted_user
        )
        if not translated_output:
            LOGGER.debug("translate embed not generated")
            return
        content, embed = translated_output
        translated_text = embed.description
        if await self.bot.embed_requested(channel):
            await channel.send(
                content,
                embed=embed,
                reference=message,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(users=False),
            )
        else:
            msg = f"{message.author}:\n{translated_text}"
            await channel.send(msg, reference=message, mention_author=False)
        if not cooldown["multiple"]:
            self.cache["translations"].append(message.id)
        guild = None
        return

    async def _check_cooldown(self, message: Union[discord.Message, int], lang: str) -> bool:
        message_id = message if isinstance(message, int) else message.id
        if message_id in self.cache["cooldown_translations"]:
            if str(lang) in self.cache["cooldown_translations"][message_id]["past_flags"]:
                return False
            if not self.cache["cooldown_translations"][message_id]["multiple"]:
                return False
            if time.time() < self.cache["cooldown_translations"][message_id]["wait"]:
                return False
        return True

    async def translate_message(
        self,
        message: discord.Message,
        to_lang: Optional[str] = None,
        flag: Optional[str] = None,
        reacted_user: Optional[discord.Member] = None,
    ) -> Optional[Tuple[str, discord.Embed]]:
        to_translate = None
        if message.embeds:
            if message.embeds[0].description:
                to_translate = message.embeds[0].description
        else:
            to_translate = message.clean_content

        if not to_translate:
            LOGGER.debug("to_translate was %s? shocking!!", to_translate)
            return None
        if flag is not None:
            num_emojis = 0
            for reaction in message.reactions:
                if reaction.emoji == str(flag):
                    num_emojis = reaction.count
            if num_emojis > 1:
                return None
            to_lang = FLAGS[str(flag)]["code"]
        try:
            detected_lang = await self._tr.detect_language(to_translate, guild=message.guild)
        except GoogleTranslateAPIError as exc:
            LOGGER.exception("Error in language detection", exc_info=exc)
            return None
        except Exception as exc:
            LOGGER.exception("Error detecting language", exc_info=exc)
            return None
        if not detected_lang:
            LOGGER.debug("detected_lang was none, abort!")
            return None
        original_lang = detected_lang.language
        author = message.author
        from_lang = str(detected_lang)
        if from_lang == to_lang:
            # don't post anything if the detected language is the same
            LOGGER.debug("to_lang is same as from_lang, abort!")
            return None
        try:
            translated_text = await self._tr.translate_text(
                str(to_lang), to_translate, original_lang, guild=message.guild
            )
        except Exception as exc:
            LOGGER.error(
                "Error while translating message in Guild=%s Channel=%s",
                message.guild, message.channel, exc_info=exc
            )
            return None
        if not translated_text:
            return None
        return translated_text.embed(author, from_lang, to_lang, reacted_user)

    @commands.Cog.listener()
    async def on_red_api_tokens_update(
        self, service_name: str, api_tokens: Mapping[str, str]
    ) -> None:
        if service_name != "google_translate":
            return
        if "api_key" not in api_tokens:
            return
        if not self._tr:
            self._tr = GoogleTranslator(
                api_tokens["api_key"], session=None, stats_counter=StatsCounter(self.config)
            )
        self._tr._api_token = api_tokens["api_key"]