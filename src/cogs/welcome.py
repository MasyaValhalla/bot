from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands

if TYPE_CHECKING:
    from bot import DsBot

from src.util import save_config
from src.views import SettingsView, build_settings_embed

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"

WELCOME_SETTINGS = [
    {"label": "Канал приветствия", "key": "welcome_channel", "emoji": "👋", "kind": "text_channel"},
    {"label": "Текст приветствия", "key": "welcome_message", "emoji": "💬", "kind": "text"},
    {"label": "Изображение", "key": "welcome_image", "emoji": "🎨", "kind": "text"},
]


def _build_welcome_embed(
    guild: discord.Guild, member: discord.Member, cfg: dict,
) -> discord.Embed:
    msg = cfg.get("welcome_message", "Добро пожаловать на сервер!")
    img = cfg.get("welcome_image")
    family = cfg.get("family_name", "Cartel")

    embed = discord.Embed(
        colour=discord.Colour.dark_red(),
        description=(
            f"**👋 {msg}**\n\n"
            f"Привет, {member.mention}!\n"
            f"Мы рады видеть тебя на сервере **{guild.name}**.\n\n"
            f"Хочешь вступить в семью — нажми кнопку ниже."
        ),
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_thumbnail(url=member.display_avatar.url)
    if img:
        embed.set_image(url=img)
    if guild.icon:
        embed.set_footer(text=family, icon_url=guild.icon.url)
    else:
        embed.set_footer(text=family)
    return embed


class _WelcomeView(ui.View):
    def __init__(self, guild_id: int, ch_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(ui.Button(
            label="📋 Подать заявку",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/channels/{guild_id}/{ch_id}",
        ))


class WelcomeCog(commands.Cog):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        print(f"[WELCOME] on_member_join: {member} (bot={member.bot})")
        if member.bot:
            return
        cfg = self.bot.config
        ch_id = cfg.get("welcome_channel")
        if not ch_id:
            print("[WELCOME] welcome_channel not set")
            return
        ch = member.guild.get_channel(ch_id)
        if not ch or not isinstance(ch, discord.TextChannel):
            print(f"[WELCOME] channel {ch_id} not found")
            return

        embed = _build_welcome_embed(member.guild, member, cfg)
        ticket_ch = cfg.get("ticket_panel_channel")
        view = _WelcomeView(member.guild.id, ticket_ch) if ticket_ch else None
        await ch.send(content=member.mention, embed=embed, view=view)
        print(f"[WELCOME] sent for {member}")

    приветствие = app_commands.Group(
        name="приветствие", description="Авто-приветствие",
        default_permissions=discord.Permissions(administrator=True),
    )

    @приветствие.command(name="настройка", description="Настройки приветствия")
    async def settings(self, interaction: discord.Interaction) -> None:
        embed = build_settings_embed("👋 Настройки приветствия", WELCOME_SETTINGS, self.bot.config)
        await interaction.response.send_message(embed=embed, view=SettingsView(self.bot, WELCOME_SETTINGS), ephemeral=True)

    @приветствие.command(name="тест", description="Тест приветствия")
    async def test(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild or not isinstance(interaction.user, discord.Member):
            return
        embed = _build_welcome_embed(guild, interaction.user, self.bot.config)
        ticket_ch = self.bot.config.get("ticket_panel_channel")
        view = _WelcomeView(guild.id, ticket_ch) if ticket_ch else None
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: DsBot) -> None:
    await bot.add_cog(WelcomeCog(bot))
