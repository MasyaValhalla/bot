from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks

if TYPE_CHECKING:
    from bot import DsBot

from src.util import save_config
from src.views import SettingsView, build_settings_embed

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"

AFK_SETTINGS = [
    {"label": "Войс АФК", "key": "afk_voice_channel", "emoji": "🔊", "kind": "voice_channel"},
    {"label": "Канал АФК-панели", "key": "afk_channel", "emoji": "💤", "kind": "text_channel"},
    {"label": "Лог АФК", "key": "log_afk", "emoji": "📋", "kind": "text_channel"},
]


class AfkModal(ui.Modal, title="Подать АФК"):
    reason_field = ui.TextInput(label="Причина", placeholder="...", max_length=256)
    duration_field = ui.TextInput(label="Время (минуты)", placeholder="30", max_length=5)

    def __init__(self, bot: DsBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        try:
            minutes = int(self.duration_field.value)
        except ValueError:
            return await interaction.response.send_message("❌ Укажите число.", ephemeral=True)
        if minutes < 1 or minutes > 1440:
            return await interaction.response.send_message("❌ От 1 до 1440 мин.", ephemeral=True)

        until = discord.utils.utcnow() + datetime.timedelta(minutes=minutes)
        await self.bot.db.deactivate_afk_by_user(interaction.user.id, guild.id)
        await self.bot.db.add_afk(interaction.user.id, guild.id, self.reason_field.value, until.isoformat(), 0)
        await interaction.response.send_message(f"✅ АФК до <t:{int(until.timestamp())}:T> ({minutes} мин.)", ephemeral=True)
        await _refresh(self.bot, guild)
        await _send_log(self.bot, guild, f"💤 {interaction.user.mention} АФК {minutes} мин. — {self.reason_field.value}")


class AfkPanelView(ui.View):
    def __init__(self, bot: DsBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="Подать АФК", emoji="💤", style=discord.ButtonStyle.primary, custom_id="afk_panel_btn")
    async def afk_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(AfkModal(self.bot))

    @ui.button(label="Вернуться", emoji="🔙", style=discord.ButtonStyle.secondary, custom_id="afk_return_btn")
    async def ret_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        guild = interaction.guild
        if not guild:
            return
        afks = await self.bot.db.get_active_afks(guild.id)
        if not any(a["user_id"] == interaction.user.id for a in afks):
            return await interaction.response.send_message("Вы не в АФК.", ephemeral=True)
        await self.bot.db.deactivate_afk_by_user(interaction.user.id, guild.id)
        await interaction.response.send_message("✅ Вы вернулись!", ephemeral=True)
        await _refresh(self.bot, guild)
        await _send_log(self.bot, guild, f"💤 {interaction.user.mention} вернулся из АФК")


async def _build_embed(bot: DsBot, guild: discord.Guild) -> discord.Embed:
    afks = await bot.db.get_active_afks(guild.id)
    embed = discord.Embed(title="💤 Список АФК", colour=discord.Colour.orange(), timestamp=discord.utils.utcnow())
    now = discord.utils.utcnow()
    lines = []
    for a in afks:
        try:
            until = datetime.datetime.fromisoformat(a["until"]).replace(tzinfo=datetime.timezone.utc)
        except (ValueError, TypeError):
            continue
        if until <= now:
            await bot.db.deactivate_afk(a["id"])
            continue
        m = guild.get_member(a["user_id"])
        name = m.mention if m else f"<@{a['user_id']}>"
        lines.append(f"{name} — {a['reason']} (до <t:{int(until.timestamp())}:T>)")
    embed.description = "\n".join(lines) or "Никто не в АФК."
    return embed


async def _refresh(bot: DsBot, guild: discord.Guild) -> None:
    ch_id = bot.config.get("afk_channel")
    if not ch_id:
        return
    ch = guild.get_channel(ch_id)
    if not ch or not isinstance(ch, discord.TextChannel):
        return
    embed = await _build_embed(bot, guild)
    async for msg in ch.history(limit=20):
        if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "💤 Список АФК":
            await msg.edit(embed=embed, view=AfkPanelView(bot))
            return


async def _send_log(bot: DsBot, guild: discord.Guild, text: str) -> None:
    ch_id = bot.config.get("log_channels", {}).get("afk")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            await ch.send(text)


class AfkCog(commands.Cog):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot
        self.cleanup.start()

    def cog_unload(self) -> None:
        self.cleanup.cancel()

    @tasks.loop(minutes=1)
    async def cleanup(self) -> None:
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            afks = await self.bot.db.get_active_afks(guild.id)
            changed = False
            for a in afks:
                try:
                    until = datetime.datetime.fromisoformat(a["until"]).replace(tzinfo=datetime.timezone.utc)
                except (ValueError, TypeError):
                    continue
                if until <= now:
                    await self.bot.db.deactivate_afk(a["id"])
                    changed = True
            if changed:
                await _refresh(self.bot, guild)

    @cleanup.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    афк = app_commands.Group(
        name="афк", description="Управление АФК",
        default_permissions=discord.Permissions(administrator=True),
    )

    @афк.command(name="панель", description="Разместить панель АФК")
    async def panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        self.bot.config["afk_channel"] = interaction.channel_id
        save_config(CONFIG_PATH, self.bot.config)
        embed = await _build_embed(self.bot, guild)
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(embed=embed, view=AfkPanelView(self.bot))  # type: ignore[union-attr]
        await interaction.followup.send("✅ Панель размещена.")

    @афк.command(name="настройка", description="Настройки АФК")
    async def settings(self, interaction: discord.Interaction) -> None:
        embed = build_settings_embed("💤 Настройки АФК", AFK_SETTINGS, self.bot.config)
        await interaction.response.send_message(embed=embed, view=SettingsView(self.bot, AFK_SETTINGS), ephemeral=True)


async def setup(bot: DsBot) -> None:
    await bot.add_cog(AfkCog(bot))
