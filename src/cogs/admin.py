from __future__ import annotations

import asyncio
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

ALL_SETTINGS = [
    {"label": "Роль рекрутов", "key": "ticket_role", "emoji": "👥", "kind": "role"},
    {"label": "Канал итогов заявок", "key": "ticket_results_channel", "emoji": "📝", "kind": "text_channel"},
    {"label": "Войс обзвона", "key": "ticket_voice_channel", "emoji": "🔊", "kind": "voice_channel"},
    {"label": "Категория тикетов", "key": "ticket_category", "emoji": "📂", "kind": "category"},
    {"label": "Изображение панели", "key": "ticket_panel_image", "emoji": "🖼️", "kind": "text"},
    {"label": "Название семьи", "key": "family_name", "emoji": "🏷️", "kind": "text"},
    {"label": "Канал приветствия", "key": "welcome_channel", "emoji": "👋", "kind": "text_channel"},
    {"label": "Текст приветствия", "key": "welcome_message", "emoji": "💬", "kind": "text"},
    {"label": "Изображение приветствия", "key": "welcome_image", "emoji": "🎨", "kind": "text"},
    {"label": "Канал автопарка", "key": "fleet_channel", "emoji": "🚗", "kind": "text_channel"},
    {"label": "Войс АФК", "key": "afk_voice_channel", "emoji": "💤", "kind": "voice_channel"},
    {"label": "Канал АФК", "key": "afk_channel", "emoji": "😴", "kind": "text_channel"},
    {"label": "Лог: тикеты", "key": "log_tickets", "emoji": "📋", "kind": "text_channel"},
    {"label": "Лог: автопарк", "key": "log_fleet", "emoji": "🚙", "kind": "text_channel"},
    {"label": "Лог: АФК", "key": "log_afk", "emoji": "🛌", "kind": "text_channel"},
    {"label": "Лог: отпуск", "key": "log_vacation", "emoji": "🏖️", "kind": "text_channel"},
]


class AdminCog(commands.Cog):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot

    @app_commands.command(name="настройка", description="Все настройки бота")
    @app_commands.default_permissions(administrator=True)
    async def settings_cmd(self, interaction: discord.Interaction) -> None:
        embed = build_settings_embed("⚙️ Настройки бота", ALL_SETTINGS, self.bot.config)
        await interaction.response.send_message(
            embed=embed, view=SettingsView(self.bot, ALL_SETTINGS), ephemeral=True,
        )

    @app_commands.command(name="сбор", description="Переместить всех в ваш войс")
    @app_commands.default_permissions(move_members=True)
    async def gather(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ Вы не в голосовом канале.", ephemeral=True)

        target = interaction.user.voice.channel
        afk_id = self.bot.config.get("afk_voice_channel")
        moved = 0
        for vc in guild.voice_channels:
            if vc.id == target.id or (afk_id and vc.id == afk_id):
                continue
            for m in vc.members:
                try:
                    await m.move_to(target)
                    moved += 1
                except discord.HTTPException:
                    pass
        await interaction.response.send_message(f"✅ Перемещено **{moved}** в {target.mention}.")

    @app_commands.command(name="рассылка", description="ЛС всем участникам роли")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(роль="Целевая роль", сообщение="Текст рассылки")
    async def broadcast(self, interaction: discord.Interaction, роль: discord.Role, сообщение: str) -> None:
        guild = interaction.guild
        if not guild:
            return
        await interaction.response.defer(ephemeral=True)
        sent = failed = 0
        for m in роль.members:
            if m.bot:
                continue
            try:
                await m.send(f"📢 **Рассылка** ({guild.name}):\n\n{сообщение}")
                sent += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1
            await asyncio.sleep(0.5)
        await interaction.followup.send(f"✅ Отправлено: **{sent}** | Не удалось: **{failed}**")

    @app_commands.command(name="синхронизация", description="Синхронизировать слеш-команды")
    @app_commands.default_permissions(administrator=True)
    async def sync_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync()
        await interaction.followup.send(f"✅ Синхронизировано **{len(synced)}** команд.")


async def setup(bot: DsBot) -> None:
    await bot.add_cog(AdminCog(bot))
