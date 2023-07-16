"""
Translator cog

Cog credit to aziz#5919 for the idea and

Links

Wiki                                               https://goo.gl/3fxjSA
GitHub                                             https://goo.gl/oQAQde
Support the developer                              https://goo.gl/Brchj4
Invite the bot to your guild                       https://goo.gl/aQm2G7
Join the official development guild                https://discord.gg/uekTNPj
"""
from io import StringIO
from typing import Any, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands.converter import FuzzyGuildConverter
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import SetApiView

from .api import GoogleTranslateAPI, GoogleTranslator, StatsCounter
from .converters import ChannelUserRole, FlagTranslation
from .errors import GoogleTranslateAPIError
from .lang_codes import ISO639_MAP

BASE_URL = "https://translation.googleapis.com"


class Translate(GoogleTranslateAPI, commands.Cog):
    """
    Translate messages using Google Translate
    """

    __authors__ = ["Aziz", "TrustyJAID"]
    __version__ = "2.6.0"

    def format_help_for_context(self, ctx: commands.Context) -> str:
        """Thanks Sinbad!"""
        pre_processed = super().format_help_for_context(ctx)
        return f"{pre_processed}\n\nCog Version: {self.__version__}"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, 156434873547, force_registration=True)
        self.config.register_guild(
            reaction=False,
            text=False,
            whitelist=[],
            blacklist=[],
            count={"characters": 0, "requests": 0, "detect": 0},
        )
        self.config.register_global(
            cooldown={"past_flags": [], "timeout": 0, "multiple": False},
            count={"characters": 0, "requests": 0, "detect": 0},
        )
        self.cache = {
            "translations": [],
            "cooldown_translations": {},
            "guild_messages": [],
            "guild_reactions": [],
            "cooldown": {},
            "guild_blacklist": {},
            "guild_whitelist": {},
        }
        self._key: Optional[str] = None
        self.translation_loop.start()
        self.translate_ctx = discord.app_commands.ContextMenu(
            name="Translate Message", callback=self.translate_from_message
        )
        self._tr: GoogleTranslator = discord.utils.MISSING

    async def red_delete_data_for_user(self, **kwargs: Any):
        """Nothing to delete"""
        return

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.translate_ctx)
        central_key = (await self.bot.get_shared_api_tokens("google_translate")).get(
            "api_key", None
        )
        self._tr = GoogleTranslator(
            central_key, session=None, stats_counter=StatsCounter(self.config)
        )
        await self._tr.stats_counter.initialize()

    async def cog_unload(self):
        self.bot.tree.remove_command(self.translate_ctx.name, type=self.translate_ctx.type)
        if self._tr is not discord.utils.MISSING:
            await self._tr.close()

    @commands.hybrid_group(fallback="text", aliases=["tl", "tr"])
    async def translate(
        self,
        ctx: commands.Context,
        to_language: FlagTranslation,
        *,
        text: str,
    ) -> None:
        """
        Translate messages with Google Translate

        `<to_language>` is the language you would like to translate
        `<text>` is the text you want to translate.
        """
        if not self._tr.has_token:
            await ctx.send("The bot owner needs to set an api key first!")
            return
        async with ctx.typing():
            try:
                detected_lang = await self._tr.detect_language(text, guild=ctx.guild)
            except GoogleTranslateAPIError as e:
                await ctx.send(str(e))
                return
            from_lang = str(detected_lang or "auto")
            if str(to_language) == from_lang:
                ln_from = ISO639_MAP.get(from_lang) or from_lang.upper()
                ln_to = ISO639_MAP.get(str(to_language)) or str(to_language).upper()
                await ctx.send(f"⚠️ I cannot translate `{ln_from}` to `{ln_to}`! Same language!?")
                return
            try:
                translated_text = await self._tr.translate_text(
                    str(to_language), text, str(from_lang), guild=ctx.guild
                )
            except GoogleTranslateAPIError as e:
                await ctx.send(str(e))
                return
            if translated_text is None:
                await ctx.send("Google said there is nothing to be translated /shrug")
                return
            assert isinstance(ctx.me, discord.Member)
            ref = ctx.message.to_reference(fail_if_not_exists=False)
            if not ctx.channel.permissions_for(ctx.me).embed_links:
                await ctx.send(str(translated_text), reference=ref, mention_author=False)
                return
            content, embed = translated_text.embed(
                ctx.author, from_lang, str(to_language), ctx.author
            )
            await ctx.send(
                content=None if ctx.interaction else content,
                embed=embed,
                reference=ref,
                mention_author=False,
                allowed_mentions=discord.AllowedMentions(users=False),
            )
            return

    @translate.group(name="set")
    async def translateset(self, ctx: commands.Context) -> None:
        """
        Toggle the bot auto translating
        """
        pass

    @translateset.command(name="settings", aliases=["showsettings"])
    async def translate_settings(
        self, ctx: commands.Context, guild: Optional[FuzzyGuildConverter],
    ):
        """
        Show the current translate settings
        """
        assert isinstance(guild, discord.Guild)
        guild_settings = await self.config.guild(guild).all()
        msg = StringIO()
        msg.write("### Server Settings:\n")
        add_keys = {
            "text": "Flag Translations",
            "reaction": "Reaction Translations",
            "whitelist": "Allowlist",
            "blacklist": "Blocklist",
            "count": "Stats",
        }
        for key, value in guild_settings.items():
            if key == "count":
                continue
            if key not in add_keys:
                continue
            key_name = add_keys[key]
            value_str = str(value)
            if key in ("whitelist", "blacklist"):
                items = []
                for _id in value:
                    try:
                        items.append(await ChannelUserRole().convert(ctx, str(_id)))
                    except commands.BadArgument:
                        continue
                value_str = "\n".join(f"- {i.mention}" for i in items)
                if value_str:
                    msg.write(f"{key_name}:\n{value_str}\n")
                continue
            msg.write(f"- {key_name}:  **{value_str}**\n")
        msg.write(await self._tr.stats_counter.text(guild))
        em = discord.Embed(description=msg.getvalue(), colour=0x7289DA)
        em.set_author(name=str(guild), icon_url=guild.icon)
        await ctx.send(embed=em)
        msg.close()
        return

    @translate.command(name="stats")
    async def translate_stats(self, ctx: commands.Context, guild_id: Optional[int]):
        """
        Shows translation usage
        """
        if guild_id and not await self.bot.is_owner(ctx.author):
            await ctx.send(("That is only available for the bot owner."))
            return
        elif guild_id and await self.bot.is_owner(ctx.author):
            if not (guild := self.bot.get_guild(guild_id)):
                await ctx.send(f"Guild `{guild_id}` not found.")
                return
        else:
            guild = ctx.guild
        if guild is None and not await self.bot.is_owner(ctx.author):
            await ctx.send(("This command is only available inside guilds."))
            return
        msg = await self._tr.stats_counter.text(guild)
        await ctx.maybe_send_embed(msg)
        return

    @translate.group(name="blocklist", aliases=["blacklist"], with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist(self, ctx: commands.Context) -> None:
        """
        Set blacklist options for translations

        blacklisting supports channels, users, or roles
        """
        pass

    @translate.group(name="allowlist", aliases=["whitelist"], with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist(self, ctx: commands.Context) -> None:
        """
        Set whitelist options for translations

        whitelisting supports channels, users, or roles
        """
        pass

    @whitelist.command(name="add", with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_add(
        self, ctx: commands.Context, *channel_user_role: ChannelUserRole
    ) -> None:
        """
        Add a channel, user, or role to translation whitelist
        """
        if len(channel_user_role) < 1:
            await ctx.send(
                ("You must supply 1 or more channels users or roles to be whitelisted.")
            )
            return

        assert ctx.guild is not None
        for obj in channel_user_role:
            if obj.id not in await self.config.guild(ctx.guild).whitelist():
                async with self.config.guild(ctx.guild).whitelist() as whitelist:
                    assert isinstance(whitelist, list)
                    whitelist.append(obj.id)

        list_type = humanize_list([c.mention for c in channel_user_role])
        await ctx.send(
            f"{list_type} added to translation whitelist.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    @whitelist.command(name="remove", aliases=["rem", "del"], with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_remove(
        self, ctx: commands.Context, *channel_user_role: ChannelUserRole
    ) -> None:
        """
        Remove a channel, user, or role from translation whitelist
        """
        if len(channel_user_role) < 1:
            await ctx.send(
                "You must supply 1 or more channels, users, "
                "or roles to be removed from the whitelist"
            )
            return

        assert ctx.guild is not None
        for obj in channel_user_role:
            if obj.id in await self.config.guild(ctx.guild).whitelist():
                async with self.config.guild(ctx.guild).whitelist() as whitelist:
                    assert isinstance(whitelist, list)
                    whitelist.remove(obj.id)

        list_type = humanize_list([c.mention for c in channel_user_role])
        await ctx.send(
            f"{list_type} removed from translation whitelist.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    @whitelist.command(name="list", with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def whitelist_list(self, ctx: commands.Context) -> None:
        """
        List Channels, Users, and Roles in the servers translation whitelist.
        """
        whitelist = []

        assert ctx.guild is not None
        for _id in await self.config.guild(ctx.guild).whitelist():
            try:
                whitelist.append(await ChannelUserRole().convert(ctx, str(_id)))
            except commands.BadArgument:
                continue
        if whitelist:
            whitelist_s = humanize_list([c.mention for c in whitelist])
            await ctx.send(
                f"The following channels, users, or roles are currently allowed: {whitelist_s}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await ctx.send(
            "There are currently no channels, users, or roles in this servers translate allowlist."
        )

    @blacklist.command(name="add", with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_add(
        self, ctx: commands.Context, *channel_user_role: ChannelUserRole
    ) -> None:
        """
        Add a channel, user, or role to translation blacklist
        """
        if len(channel_user_role) < 1:
            await ctx.send(
                ("You must supply 1 or more channels users or roles to be blacklisted.")
            )
            return

        assert ctx.guild is not None
        for obj in channel_user_role:
            if obj.id not in await self.config.guild(ctx.guild).blacklist():
                async with self.config.guild(ctx.guild).blacklist() as blacklist:
                    assert isinstance(blacklist, list)
                    blacklist.append(obj.id)

        list_type = humanize_list([c.mention for c in channel_user_role])
        await ctx.send(
            f"{list_type} added to translation blacklist.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    @blacklist.command(name="remove", aliases=["rem", "del"], with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_remove(
        self, ctx: commands.Context, *channel_user_role: ChannelUserRole
    ) -> None:
        """
        Remove a channel, user, or role from translation blacklist
        """
        if len(channel_user_role) < 1:
            await ctx.send(
                "You must supply 1 or more channels, users, "
                "or roles to be removed from the blacklist"
            )
            return

        assert ctx.guild is not None
        for obj in channel_user_role:
            if obj.id in await self.config.guild(ctx.guild).blacklist():
                async with self.config.guild(ctx.guild).blacklist() as blacklist:
                    assert isinstance(blacklist, list)
                    blacklist.remove(obj.id)

        list_type = humanize_list([c.mention for c in channel_user_role])
        await ctx.send(
            f"{list_type} removed from translation blacklist.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    @blacklist.command(name="list", with_app_command=False)
    @commands.mod_or_permissions(manage_messages=True)
    @commands.guild_only()
    async def blacklist_list(self, ctx: commands.Context) -> None:
        """
        List Channels, Users, and Roles in the servers translation blacklist.
        """
        blacklist = []
        assert ctx.guild is not None
        for _id in await self.config.guild(ctx.guild).blacklist():
            try:
                blacklist.append(await ChannelUserRole().convert(ctx, str(_id)))
            except commands.BadArgument:
                continue
        if blacklist:
            blacklist_s = humanize_list([x.mention for x in blacklist])
            await ctx.send(
                f"The following channels, users, or roles are currently blocked: {blacklist_s}",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await ctx.send(
            "There are currently no channels, users, or roles in this servers translate blocklist."
        )

    @translateset.command(aliases=["reaction", "reactions"])
    @commands.mod_or_permissions(manage_channels=True)
    @commands.guild_only()
    async def react(self, ctx: commands.Context) -> None:
        """
        Toggle translations to flag emoji reactions
        """
        assert ctx.guild is not None
        toggle = not await self.config.guild(ctx.guild).reaction()
        verb = "✅ ON" if toggle else "❌ OFF"
        if not toggle and ctx.guild.id in self.cache["guild_reactions"]:
            self.cache["guild_reactions"].remove(ctx.guild.id)
        await self.config.guild(ctx.guild).reaction.set(toggle)
        await ctx.send(f"Reaction translations have been turned {verb}")
        return

    @translateset.command(aliases=["multi"], with_app_command=False)
    @commands.is_owner()
    @commands.guild_only()
    async def multiple(self, ctx: commands.Context) -> None:
        """
        Toggle multiple translations for the same message

        This will also ignore the translated message from
        being translated into another language
        """
        toggle = not await self.config.cooldown.multiple()  # type: ignore
        verb = "✅ ON" if toggle else "❌ OFF"
        await self.config.cooldown.multiple.set(toggle)  # type: ignore
        self.cache["cooldown"] = await self.config.cooldown()
        await ctx.send(f"Multiple translations have been turned {verb}")
        return

    @translateset.command(aliases=["cooldown"], with_app_command=False)
    @commands.is_owner()
    @commands.guild_only()
    async def timeout(self, ctx: commands.Context, time: int) -> None:
        """
        Set the cooldown before a message can be reacted to again
        for translation

        - `<time>` Number of seconds until that message can be reacted to again

        Note: If multiple reactions are not allowed the timeout setting
        is ignored until the cache cleanup ~10 minutes.
        """
        await self.config.cooldown.timeout.set(time)  # type: ignore
        self.cache["cooldown"] = await self.config.cooldown()
        await ctx.send(f"Translation timeout set to {time}s.")
        return

    #  @translateset.command(aliases=["flags"])
    #  @commands.mod_or_permissions(manage_channels=True)
    #  @commands.guild_only()
    async def flag(self, ctx: commands.Context) -> None:
        """
        Toggle translations with flag emojis in text.

        This enables automatically translating messages containing
        a valid flag emoji and the flags language is different from
        the message content.
        """
        assert ctx.guild is not None
        toggle = not await self.config.guild(ctx.guild).text()
        verb = "✅ ON" if toggle else "❌ OFF"
        if not toggle and ctx.guild.id in self.cache["guild_messages"]:
            self.cache["guild_messages"].remove(ctx.guild.id)
        await self.config.guild(ctx.guild).text.set(toggle)
        await ctx.send(f"Flag emoji translations have been turned {verb}")
        return

    @translateset.command(with_app_command=False)
    @commands.is_owner()
    async def creds(self, ctx: commands.Context) -> None:
        """
        You must get an API key from Google to set this up

        Note: Using this cog costs money, current rates are $20 per 1 million characters.
        """
        msg = (
            "1. Go to [Google Developers Console](https://console.developers.google.com/)"
            " and log in with your Google account.\n"
            "2. You should be prompted to create a new project (name does not matter)\n"
            "3. Click on Enable APIs and Services at the top.\n"
            "4. In the list of APIs choose or search for Cloud Translate API and click on it."
            " Choose Enable.\n"
            "5. Click on Credentials on the left navigation bar.\n"
            "6. Click on Create Credential at the top.\n"
            '7. At the top click the link for "API key".\n'
            "8. No application restrictions are needed. Click Create at the bottom.\n"
            "9. You now have a key to add to via "
            f"`{ctx.clean_prefix}set api google_translate api_key,YOUR_KEY_HERE`\n"
        )
        keys = {"api_key": ""}
        view = SetApiView("google_translate", keys)
        if await ctx.embed_requested():
            em = discord.Embed(description=msg)
            await ctx.send(embed=em, view=view)
            return
        msg = await ctx.send(msg, view=view)
        await view.wait()
        await msg.edit(view=None)
        return