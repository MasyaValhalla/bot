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

FLEET_SETTINGS = [
    {"label": "Канал автопарка", "key": "fleet_channel", "emoji": "🚗", "kind": "text_channel"},
    {"label": "Лог автопарка", "key": "log_fleet", "emoji": "🚙", "kind": "text_channel"},
]


async def _build_embed(bot: DsBot, guild: discord.Guild) -> discord.Embed:
    cars = await bot.db.get_cars(guild.id)
    embed = discord.Embed(
        title="🚗 Автопарк",
        colour=discord.Colour.blue(),
        timestamp=discord.utils.utcnow(),
    )
    if not cars:
        embed.description = "Автопарк пуст."
        return embed
    lines = []
    for car in cars:
        plate = f" `{car['plate']}`" if car.get("plate") else ""
        if car["taken_by"]:
            m = guild.get_member(car["taken_by"])
            who = m.mention if m else f"<@{car['taken_by']}>"
            lines.append(f"🔴 **[{car['id']}] {car['name']}**{plate} — занят {who}")
        else:
            lines.append(f"🟢 **[{car['id']}] {car['name']}**{plate} — свободен")
    embed.description = "\n".join(lines)
    embed.set_footer(text="🔑 Взять / 🔄 Вернуть — кнопки ниже")
    return embed


async def _refresh(bot: DsBot, guild: discord.Guild) -> None:
    ch_id = bot.config.get("fleet_channel")
    if not ch_id:
        return
    ch = guild.get_channel(ch_id)
    if not ch or not isinstance(ch, discord.TextChannel):
        return
    embed = await _build_embed(bot, guild)
    async for msg in ch.history(limit=20):
        if msg.author == guild.me and msg.embeds and msg.embeds[0].title == "🚗 Автопарк":
            await msg.edit(embed=embed, view=FleetPanelView(bot))
            return


async def _announce(bot: DsBot, guild: discord.Guild, text: str) -> None:
    """Public announcement in the fleet channel (with ping)."""
    ch_id = bot.config.get("fleet_channel")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            await ch.send(text, delete_after=5)


async def _send_log(bot: DsBot, guild: discord.Guild, text: str) -> None:
    ch_id = bot.config.get("log_channels", {}).get("fleet")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            await ch.send(text)


# ── Dynamic selects (not persistent — created on button click) ──

class _TakeSelect(ui.Select):
    def __init__(self, bot: DsBot, cars: list[dict]) -> None:
        self.bot = bot
        free = [c for c in cars if not c["taken_by"]]
        options = [
            discord.SelectOption(
                label=f"{c['name']}" + (f" ({c['plate']})" if c.get("plate") else ""),
                value=str(c["id"]),
                description=f"ID: {c['id']}",
            )
            for c in free
        ]
        super().__init__(placeholder="Выберите ТС...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        car_id = int(self.values[0])
        ok = await self.bot.db.take_car(car_id, interaction.user.id)
        if not ok:
            return await interaction.response.edit_message(
                content="❌ ТС уже занято — попробуйте другое.", view=None
            )
        car = await self.bot.db.get_car(car_id)
        await interaction.response.edit_message(
            content=f"✅ Вы взяли **{car['name']}**!", view=None
        )
        await _refresh(self.bot, guild)
        plate = f" `{car['plate']}`" if car.get("plate") else ""
        await _announce(self.bot, guild, f"🔑 {interaction.user.mention} взял **{car['name']}**{plate}")
        await _send_log(self.bot, guild, f"🔑 {interaction.user.mention} взял **{car['name']}**{plate}")


class _ReleaseSelect(ui.Select):
    def __init__(self, bot: DsBot, user_cars: list[dict]) -> None:
        self.bot = bot
        options = [
            discord.SelectOption(
                label=f"{c['name']}" + (f" ({c['plate']})" if c.get("plate") else ""),
                value=str(c["id"]),
                description=f"ID: {c['id']}",
            )
            for c in user_cars
        ]
        super().__init__(placeholder="Выберите ТС для возврата...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        car_id = int(self.values[0])
        ok = await self.bot.db.release_car(car_id, interaction.user.id)
        if not ok:
            return await interaction.response.edit_message(
                content="❌ Не удалось вернуть — это не ваше ТС.", view=None
            )
        car = await self.bot.db.get_car(car_id)
        await interaction.response.edit_message(
            content=f"✅ Вы вернули **{car['name']}**!", view=None
        )
        await _refresh(self.bot, guild)
        plate = f" `{car['plate']}`" if car.get("plate") else ""
        await _announce(self.bot, guild, f"🔄 {interaction.user.mention} вернул **{car['name']}**{plate}")
        await _send_log(self.bot, guild, f"🔄 {interaction.user.mention} вернул **{car['name']}**{plate}")


# ── Persistent panel view ────────────────────────────────────

class FleetPanelView(ui.View):
    def __init__(self, bot: DsBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="Взять ТС", emoji="🔑", style=discord.ButtonStyle.success, custom_id="fleet_take_btn")
    async def take_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        guild = interaction.guild
        if not guild:
            return
        cars = await self.bot.db.get_cars(guild.id)
        free = [c for c in cars if not c["taken_by"]]
        if not free:
            return await interaction.response.send_message("❌ Все ТС сейчас заняты.", ephemeral=True)
        view = ui.View(timeout=60)
        view.add_item(_TakeSelect(self.bot, cars))
        await interaction.response.send_message("Выберите свободное ТС:", view=view, ephemeral=True)

    @ui.button(label="Вернуть ТС", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="fleet_return_btn")
    async def return_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        guild = interaction.guild
        if not guild:
            return
        cars = await self.bot.db.get_cars(guild.id)
        user_cars = [c for c in cars if c["taken_by"] == interaction.user.id]
        if not user_cars:
            return await interaction.response.send_message("У вас нет взятых ТС.", ephemeral=True)
        view = ui.View(timeout=60)
        view.add_item(_ReleaseSelect(self.bot, user_cars))
        await interaction.response.send_message("Выберите ТС для возврата:", view=view, ephemeral=True)


# ── Cog ─────────────────────────────────────────────────────

class FleetCog(commands.Cog):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot

    автопарк = app_commands.Group(
        name="автопарк",
        description="Управление автопарком",
        default_permissions=discord.Permissions(administrator=True),
    )

    @автопарк.command(name="панель", description="Разместить панель автопарка в канале")
    async def panel(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        self.bot.config["fleet_channel"] = interaction.channel_id
        save_config(CONFIG_PATH, self.bot.config)
        embed = await _build_embed(self.bot, guild)
        await interaction.response.defer(ephemeral=True)
        await interaction.channel.send(embed=embed, view=FleetPanelView(self.bot))  # type: ignore[union-attr]
        await interaction.followup.send("✅ Панель автопарка размещена.")

    @автопарк.command(name="добавить", description="Добавить ТС в автопарк")
    @app_commands.describe(название="Название ТС", номер="Гос. номер (необязательно)")
    async def add(self, interaction: discord.Interaction, название: str, номер: str = "") -> None:
        guild = interaction.guild
        if not guild:
            return
        car_id = await self.bot.db.add_car(guild.id, название, номер)
        await interaction.response.send_message(
            f"✅ ТС **{название}** добавлено (ID: `{car_id}`).", ephemeral=True
        )
        await _refresh(self.bot, guild)
        await _send_log(self.bot, guild, f"➕ {interaction.user.mention} добавил ТС **{название}** (ID: {car_id})")

    @автопарк.command(name="удалить", description="Удалить ТС по ID")
    @app_commands.describe(id="ID ТС (число из списка)")
    async def remove(self, interaction: discord.Interaction, id: int) -> None:
        guild = interaction.guild
        if not guild:
            return
        car = await self.bot.db.get_car(id)
        if not car or car.get("guild_id") != guild.id:
            return await interaction.response.send_message("❌ ТС не найдено.", ephemeral=True)
        await self.bot.db.remove_car(id)
        await interaction.response.send_message(f"✅ ТС **{car['name']}** удалено.", ephemeral=True)
        await _refresh(self.bot, guild)
        await _send_log(self.bot, guild, f"➖ {interaction.user.mention} удалил ТС **{car['name']}**")

    @автопарк.command(name="принудительно-вернуть", description="Принудительно освободить ТС (для модераторов)")
    @app_commands.describe(id="ID ТС")
    async def force_release(self, interaction: discord.Interaction, id: int) -> None:
        guild = interaction.guild
        if not guild:
            return
        car = await self.bot.db.get_car(id)
        if not car or car.get("guild_id") != guild.id:
            return await interaction.response.send_message("❌ ТС не найдено.", ephemeral=True)
        await self.bot.db.force_release_car(id)
        await interaction.response.send_message(f"✅ ТС **{car['name']}** принудительно освобождено.", ephemeral=True)
        await _refresh(self.bot, guild)
        await _send_log(self.bot, guild, f"🔧 {interaction.user.mention} принудительно вернул **{car['name']}**")

    @автопарк.command(name="список", description="Показать список всех ТС")
    async def list_cars(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return
        embed = await _build_embed(self.bot, guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @автопарк.command(name="настройка", description="Настройки автопарка")
    async def settings(self, interaction: discord.Interaction) -> None:
        embed = build_settings_embed("🚗 Настройки автопарка", FLEET_SETTINGS, self.bot.config)
        await interaction.response.send_message(
            embed=embed, view=SettingsView(self.bot, FLEET_SETTINGS), ephemeral=True
        )


async def setup(bot: DsBot) -> None:
    await bot.add_cog(FleetCog(bot))
