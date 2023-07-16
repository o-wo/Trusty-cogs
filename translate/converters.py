import re
from typing import List, Mapping, Pattern, Union

import discord
from discord import app_commands
from rapidfuzz import fuzz, process, utils
from redbot.core import commands

from .flags import FLAGS
from .lang_codes import ISO639_MAP

CHANNEL_MENTION_RE: Pattern[str] = re.compile(r"<#([0-9]+)>$")
MEMBER_MENTION_RE: Pattern[str] = re.compile(r"<@!?([0-9]+)>$")
ROLE_MENTION_RE: Pattern[str] = re.compile(r"<@&([0-9]+)>$")


class ChannelUserRole(commands.IDConverter):
    """
    This will check to see if the provided argument is a channel, user, or role

    Guidance code on how to do this from:
    https://github.com/Rapptz/discord.py/blob/rewrite/discord/ext/commands/converter.py#L85
    https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/mod/mod.py#L24
    """

    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> Union[discord.abc.GuildChannel, discord.Thread, discord.Role, discord.Member]:
        result = None
        assert ctx.guild is not None
        id_match = self._get_id_match(argument)
        channel_match = CHANNEL_MENTION_RE.match(argument)
        member_match = MEMBER_MENTION_RE.match(argument)
        role_match = ROLE_MENTION_RE.match(argument)
        for converter in ["channel", "role", "member"]:
            if converter == "channel":
                match = id_match or channel_match
                if match:
                    channel_id = match.group(1)
                    result = ctx.guild.get_channel_or_thread(int(channel_id))
                else:
                    result = discord.utils.get(ctx.guild.text_channels, name=argument)
            if converter == "member":
                match = id_match or member_match
                if match:
                    member_id = match.group(1)
                    result = ctx.guild.get_member(int(member_id))
                else:
                    result = ctx.guild.get_member_named(argument)
            if converter == "role":
                match = id_match or role_match
                if match:
                    role_id = match.group(1)
                    result = ctx.guild.get_role(int(role_id))
                else:
                    result = discord.utils.get(ctx.guild._roles.values(), name=argument)
            if result:
                break
        if not result:
            raise commands.BadArgument(f"{argument} is not a valid channel, user or role ID.")
        return result


MAP: Mapping[str, str] = {"en-US": "en", "en-GB": "en", "pt-BR": "pt", "es-ES": "es", "sv-SE": "sv"}


class FlagTranslation(discord.app_commands.Transformer):
    """
    This will convert flags and languages to the correct code to be used by the API

    Guidance code on how to do this from:
    https://github.com/Rapptz/discord.py/blob/rewrite/discord/ext/commands/converter.py#L85
    https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/redbot/cogs/mod/mod.py#L24

    """

    async def convert(self, ctx: commands.Context, argument: str) -> str:
        result = ""
        # match by country flag unicode
        if argument in FLAGS:
            result = FLAGS[argument]["code"]
        for _, value in FLAGS.items():
            if len(argument) == 2 and argument.lower() in value["code"]:
                result = value["code"]
                break
            if value["code"].lower() == argument.casefold():
                result = value['code']
                break
            if len(argument) > 2 and argument.lower() in value["name"].lower():
                result = value["code"]
                break
            if argument.casefold() == value["country"].lower():
                result = value["code"]
                break
        if result:
            return result
        # match by 2-3 letter language code
        if ISO639_MAP.get(argument):
            result = argument
        if ISO639_MAP.get(argument.lower()):
            result = argument.lower()
        # fuzzy match by language name given
        if lang := process.extractOne(
            argument,
            ISO639_MAP.values(),
            score_cutoff=80,
            scorer=fuzz.WRatio,
            processor=utils.default_process,
        ):
            # next(iter([k for k, v in ISO639_MAP.items() if v == lang[0]]), None)
            result = {v: k for k, v in ISO639_MAP.items()}.get(lang[0], "en")
        # make it default to english
        if not result:
            result = "en"
            # raise commands.BadArgument(f'Language `{argument}` not found!')
        return result

    async def transform(self, inter: discord.Interaction, argument: str) -> str:
        ctx = await commands.Context.from_interaction(inter)
        return await self.convert(ctx, argument)

    async def autocomplete(
        self, i: discord.Interaction, argument: int | float | str
    ) -> List[discord.app_commands.Choice]:
        if not argument:
            code: str = MAP.get(i.locale.value, i.locale.value)
            name: str = i.locale.name.replace("_", " ").title()
            return [discord.app_commands.Choice(name=name, value=code)]
        options = [
            app_commands.Choice(name=i["name"], value=i["code"])
            for i in FLAGS.values()
            if (
                str(argument).lower() in i["name"].lower()
                or str(argument).lower() in i["code"].lower()
            )
        ]
        if not options:
            return [discord.app_commands.Choice(name="English", value="en")]
        return list(set(options))[:25]