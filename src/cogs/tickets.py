from __future__ import annotations

import datetime
import json
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

TICKET_TYPE_LABELS = {
    "family": "На Вступление в семью",
    "vzp": "VZP",
}

DEFAULT_QUESTIONS: list[dict] = [
    {"label": "Ваше имя (RP)", "placeholder": "Иван Иванов", "style": "short"},
    {"label": "Ваш возраст", "placeholder": "18", "style": "short"},
    {"label": "Расскажите о себе", "placeholder": "Опыт, почему хотите к нам...", "style": "long"},
]

TICKET_SETTINGS = [
    {"label": "Роль рекрутов", "key": "ticket_role", "emoji": "👥", "kind": "role"},
    {"label": "Канал итогов заявок", "key": "ticket_results_channel", "emoji": "📝", "kind": "text_channel"},
    {"label": "Войс обзвона", "key": "ticket_voice_channel", "emoji": "🔊", "kind": "voice_channel"},
    {"label": "Категория тикетов", "key": "ticket_category", "emoji": "📂", "kind": "category"},
    {"label": "Изображение панели", "key": "ticket_panel_image", "emoji": "🖼️", "kind": "text"},
    {"label": "Название семьи", "key": "family_name", "emoji": "🏷️", "kind": "text"},
    {"label": "Лог тикетов", "key": "log_tickets", "emoji": "📋", "kind": "text_channel"},
]


def _get_questions(bot: DsBot, ticket_type: str) -> list[dict]:
    qs = bot.config.get("ticket_questions", {}).get(ticket_type)
    return qs[:5] if qs else DEFAULT_QUESTIONS[:5]


# ── Dynamic modal ───────────────────────────────────────────

class DynamicTicketModal(ui.Modal):
    def __init__(self, bot: DsBot, ticket_type: str) -> None:
        label = TICKET_TYPE_LABELS.get(ticket_type, ticket_type)
        super().__init__(title=f"Заявка — {label}"[:45])
        self.bot = bot
        self.ticket_type = ticket_type
        for i, q in enumerate(_get_questions(bot, ticket_type)):
            style = discord.TextStyle.paragraph if q.get("style") == "long" else discord.TextStyle.short
            self.add_item(ui.TextInput(
                label=q["label"][:45],
                placeholder=q.get("placeholder", "")[:100],
                style=style,
                max_length=1024 if q.get("style") == "long" else 256,
                required=True,
                custom_id=f"tq_{i}",
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if not guild:
            return

        answers = {c.label: c.value for c in self.children if isinstance(c, ui.TextInput) and c.value}
        cfg = self.bot.config
        category = guild.get_channel(cfg.get("ticket_category")) if cfg.get("ticket_category") else None
        role_id = cfg.get("ticket_role")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        if role_id:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        tag = "vzp" if self.ticket_type == "vzp" else "заявка"
        ch = await guild.create_text_channel(name=f"{tag}-{interaction.user.display_name}", category=category, overwrites=overwrites)  # type: ignore[arg-type]

        tid = await self.bot.db.create_ticket(
            user_id=interaction.user.id, channel_id=ch.id,
            ticket_type=self.ticket_type,
            answers_json=json.dumps(answers, ensure_ascii=False),
        )

        label = TICKET_TYPE_LABELS.get(self.ticket_type, self.ticket_type)
        embed = discord.Embed(title=f"Заявка #{tid} — {label}", colour=discord.Colour.blurple(), timestamp=discord.utils.utcnow())
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        for ql, qv in answers.items():
            embed.add_field(name=ql, value=qv, inline=len(qv) < 50)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        ping = f"<@&{role_id}> " if role_id else ""
        await ch.send(content=f"{ping}{interaction.user.mention}", embed=embed, view=TicketControlView(self.bot))
        await interaction.response.send_message(f"✅ Тикет создан: {ch.mention}", ephemeral=True)
        await _send_log(self.bot, guild, f"📩 **Тикет** #{tid} ({label}) от {interaction.user.mention} → {ch.mention}")


class DenyReasonModal(ui.Modal, title="Причина отказа"):
    reason_field = ui.TextInput(label="Причина", placeholder="...", max_length=512)

    def __init__(self, bot: DsBot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _close_ticket(self.bot, interaction, "denied", self.reason_field.value)


# ── Panel views ─────────────────────────────────────────────

class TicketSelect(ui.Select):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot
        name = bot.config.get("family_name", "Cartel")
        super().__init__(
            placeholder="Ваш выбор", min_values=1, max_values=1,
            custom_id="ticket_panel_select",
            options=[
                discord.SelectOption(label=f"Заполнить анкету – {name}", emoji="📋", value="family"),
                discord.SelectOption(label="VZP", emoji="🎖️", value="vzp"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(DynamicTicketModal(self.bot, self.values[0]))


class TicketPanelView(ui.View):
    def __init__(self, bot: DsBot) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketSelect(bot))


class TicketControlView(ui.View):
    def __init__(self, bot: DsBot) -> None:
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="Принять", emoji="✅", style=discord.ButtonStyle.success, custom_id="ticket_accept")
    async def accept(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await _close_ticket(self.bot, interaction, "accepted")

    @ui.button(label="Отказать", emoji="❌", style=discord.ButtonStyle.danger, custom_id="ticket_deny")
    async def deny(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.send_modal(DenyReasonModal(self.bot))

    @ui.button(label="Вызвать на обзвон", emoji="🔊", style=discord.ButtonStyle.primary, custom_id="ticket_call")
    async def call(self, interaction: discord.Interaction, button: ui.Button) -> None:
        guild = interaction.guild
        if not guild:
            return
        ticket = await self.bot.db.get_ticket_by_channel(interaction.channel_id)  # type: ignore[arg-type]
        if not ticket:
            return await interaction.response.send_message("Тикет не найден.", ephemeral=True)
        vc_id = self.bot.config.get("ticket_voice_channel")
        if not vc_id:
            return await interaction.response.send_message("Войс не настроен.", ephemeral=True)
        member = guild.get_member(ticket["user_id"])
        if member:
            try:
                await member.send(f"🔊 **Вас вызывают на обзвон!**\nКанал: <#{vc_id}>")
            except discord.Forbidden:
                pass
        await interaction.response.send_message(f"📞 {interaction.user.mention} вызвал <@{ticket['user_id']}> → <#{vc_id}>")


# ── Close ticket ────────────────────────────────────────────

async def _close_ticket(bot: DsBot, interaction: discord.Interaction, result: str, deny_reason: str | None = None) -> None:
    guild = interaction.guild
    if not guild:
        return
    ticket = await bot.db.close_ticket(interaction.channel_id, interaction.user.id, result, deny_reason)  # type: ignore[arg-type]
    if not ticket:
        if not interaction.response.is_done():
            await interaction.response.send_message("Тикет не найден.", ephemeral=True)
        return

    accepted = result == "accepted"
    label = TICKET_TYPE_LABELS.get(ticket.get("ticket_type", "family"), "В семью")
    member = guild.get_member(ticket["user_id"])

    re = discord.Embed(colour=discord.Colour.green() if accepted else discord.Colour.red())
    if member:
        re.set_thumbnail(url=member.display_avatar.url)
    lines = [f"**Заявка от пользователя** <@{ticket['user_id']}>", "", f"**{label} была рассмотрена!** {'✅' if accepted else '❌'}"]
    if accepted:
        vc = bot.config.get("ticket_voice_channel")
        if vc:
            lines += ["", f"Для обзвона ожидаем в канале:", f"<#{vc}>"]
    elif deny_reason:
        lines += ["", f"**Причина:** {deny_reason}"]
    lines += ["", f"**Рассматривал заявку:** {interaction.user.mention}"]
    re.description = "\n".join(lines)

    rch_id = bot.config.get("ticket_results_channel")
    if rch_id:
        rch = guild.get_channel(rch_id)
        if rch and isinstance(rch, discord.TextChannel):
            await rch.send(content=f"<@{ticket['user_id']}>", embed=re)

    if member:
        dm = f"✅ Заявка **{label}** принята!" if accepted else f"❌ Заявка **{label}** отклонена."
        if not accepted and deny_reason:
            dm += f"\n**Причина:** {deny_reason}"
        if accepted and bot.config.get("ticket_voice_channel"):
            dm += f"\nОжидаем в <#{bot.config['ticket_voice_channel']}>"
        try:
            await member.send(dm)
        except discord.Forbidden:
            pass

    txt = "✅ ПРИНЯТА" if accepted else "❌ ОТКАЗАНО"
    if not interaction.response.is_done():
        await interaction.response.send_message(f"{txt} — канал удалится через 10 сек.")
    else:
        await interaction.followup.send(f"{txt} — канал удалится через 10 сек.")

    v = TicketControlView(bot)
    for i in v.children:
        if isinstance(i, ui.Button):
            i.disabled = True
    try:
        if interaction.message:
            await interaction.message.edit(view=v)
    except Exception:
        pass

    await _send_log(bot, guild, f"{'✅' if accepted else '❌'} **Заявка #{ticket['id']}** — {txt} {interaction.user.mention}" + (f" | {deny_reason}" if deny_reason else ""))
    await discord.utils.sleep_until(discord.utils.utcnow() + datetime.timedelta(seconds=10))
    try:
        await interaction.channel.delete()  # type: ignore[union-attr]
    except discord.HTTPException:
        pass


async def _send_log(bot: DsBot, guild: discord.Guild, text: str) -> None:
    ch_id = bot.config.get("log_channels", {}).get("tickets")
    if ch_id:
        ch = guild.get_channel(ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            await ch.send(text)


# ── Cog with slash command group ────────────────────────────

class TicketsCog(commands.Cog):
    def __init__(self, bot: DsBot) -> None:
        self.bot = bot
        bot.add_view(TicketControlView(bot))

    ticket = app_commands.Group(
        name="тикет", description="Управление тикетами",
        default_permissions=discord.Permissions(administrator=True),
    )

    @ticket.command(name="панель", description="Разместить панель тикетов")
    async def panel(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.config
        cfg["ticket_panel_channel"] = interaction.channel_id
        save_config(CONFIG_PATH, cfg)

        family = cfg.get("family_name", "Cartel")
        img = cfg.get("ticket_panel_image")
        res_ch = cfg.get("ticket_results_channel")

        embed_img = discord.Embed(colour=discord.Colour.dark_red())
        if img:
            embed_img.set_image(url=img)

        embed_text = discord.Embed(colour=discord.Colour.dark_red())
        lines = ["**👋 Путь в семью начинается здесь!**", "", "Обязательно ознакомьтесь с условиями:",
                 "› Требуется персонаж 5 уровня на сервере.",
                 "› Нет слотов для онли VZP — в семье играется весь контент.",
                 "› Мы не принимаем игроков, обходящих ЧС через сторонние сошники."]
        if res_ch:
            lines += ["", f"*Если ЛС недоступны — итоги в <#{res_ch}>*"]
        embed_text.description = "\n".join(lines)
        embed_text.set_footer(text=family)
        g = interaction.guild
        if g and g.icon:
            embed_text.set_footer(text=family, icon_url=g.icon.url)

        await interaction.response.defer(ephemeral=True)
        embeds = [embed_img, embed_text] if img else [embed_text]
        await interaction.channel.send(embeds=embeds, view=TicketPanelView(self.bot))  # type: ignore[union-attr]
        await interaction.followup.send("✅ Панель размещена.")

    @ticket.command(name="настройка", description="Настройки тикет-системы")
    async def settings(self, interaction: discord.Interaction) -> None:
        embed = build_settings_embed("🎫 Настройки тикетов", TICKET_SETTINGS, self.bot.config)
        await interaction.response.send_message(embed=embed, view=SettingsView(self.bot, TICKET_SETTINGS), ephemeral=True)

    @ticket.command(name="вопросы", description="Показать вопросы анкеты")
    @app_commands.describe(тип="Тип анкеты")
    @app_commands.choices(тип=[app_commands.Choice(name="Семья", value="family"), app_commands.Choice(name="VZP", value="vzp")])
    async def questions(self, interaction: discord.Interaction, тип: str = "family") -> None:
        qs = _get_questions(self.bot, тип)
        label = TICKET_TYPE_LABELS.get(тип, тип)
        embed = discord.Embed(title=f"📋 Вопросы — {label}", colour=discord.Colour.blurple())
        lines = []
        for i, q in enumerate(qs, 1):
            s = "📝 длинный" if q.get("style") == "long" else "✏️ короткий"
            lines.append(f"**{i}.** {q['label']} ({s})")
        embed.description = "\n".join(lines) or "Нет вопросов."
        embed.set_footer(text="Макс. 5 вопросов")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket.command(name="добавить-вопрос", description="Добавить вопрос в анкету")
    @app_commands.describe(тип="Тип анкеты", стиль="Стиль поля", текст="Текст вопроса", подсказка="Текст-подсказка")
    @app_commands.choices(
        тип=[app_commands.Choice(name="Семья", value="family"), app_commands.Choice(name="VZP", value="vzp")],
        стиль=[app_commands.Choice(name="Короткий", value="short"), app_commands.Choice(name="Длинный", value="long")],
    )
    async def add_q(self, interaction: discord.Interaction, тип: str, стиль: str, текст: str, подсказка: str = "") -> None:
        qs = self.bot.config.setdefault("ticket_questions", {}).setdefault(тип, [])
        if len(qs) >= 5:
            return await interaction.response.send_message("❌ Максимум 5 вопросов.", ephemeral=True)
        qs.append({"label": текст[:45], "placeholder": подсказка[:100], "style": стиль})
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.send_message(f"✅ Вопрос добавлен: **{текст}**", ephemeral=True)

    @ticket.command(name="удалить-вопрос", description="Удалить вопрос по номеру")
    @app_commands.describe(тип="Тип анкеты", номер="Номер вопроса")
    @app_commands.choices(тип=[app_commands.Choice(name="Семья", value="family"), app_commands.Choice(name="VZP", value="vzp")])
    async def del_q(self, interaction: discord.Interaction, тип: str, номер: int) -> None:
        qs = self.bot.config.get("ticket_questions", {}).get(тип, [])
        if номер < 1 or номер > len(qs):
            return await interaction.response.send_message(f"❌ Нет вопроса #{номер}.", ephemeral=True)
        r = qs.pop(номер - 1)
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.send_message(f"✅ Удалён: **{r['label']}**", ephemeral=True)

    @ticket.command(name="сброс-вопросов", description="Сбросить вопросы на стандартные")
    @app_commands.choices(тип=[app_commands.Choice(name="Семья", value="family"), app_commands.Choice(name="VZP", value="vzp")])
    async def reset_q(self, interaction: discord.Interaction, тип: str = "family") -> None:
        self.bot.config.setdefault("ticket_questions", {})[тип] = []
        save_config(CONFIG_PATH, self.bot.config)
        await interaction.response.send_message("✅ Вопросы сброшены.", ephemeral=True)


async def setup(bot: DsBot) -> None:
    await bot.add_cog(TicketsCog(bot))
