"""Bot Greetings"""
# Copyright (C) 2020 - 2023  UserbotIndo Team, <https://github.com/userbotindo.git>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
from html import escape
from typing import (
    Any,
    Callable,
    ClassVar,
    Coroutine,
    List,
    MutableMapping,
    Optional,
    Tuple,
    Union,
)

from pyrogram.client import Client
from pyrogram.enums.parse_mode import ParseMode
from pyrogram.enums.chat_member_status import ChatMemberStatus
from pyrogram.errors import (
    ChannelPrivate,
    ChatWriteForbidden,
    MediaEmpty,
    MessageDeleteForbidden,
    MessageEmpty,
)
from pyrogram.types import Chat, ChatMemberUpdated, Message, User
from pyrogram.types.messages_and_media.message import Str

from anjani import command, filters, plugin, util
from anjani.util.tg import (
    Button,
    Types,
    build_button,
    get_message_info,
    parse_button,
    revert_button,
)


class Greeting(plugin.Plugin):
    name: ClassVar[str] = "Greetings"
    helpable: ClassVar[bool] = True

    db: util.db.AsyncCollection
    chat_db: util.db.AsyncCollection
    SEND: MutableMapping[int, Callable[..., Coroutine[Any, Any, Optional[Message]]]]

    async def on_load(self) -> None:
        self.db = self.bot.db.get_collection("WELCOME")
        self.chat_db = self.bot.db.get_collection("CHATS")

        self.SEND = {
            Types.TEXT.value: self.bot.client.send_message,
            Types.BUTTON_TEXT.value: self.bot.client.send_message,
            Types.DOCUMENT.value: self.bot.client.send_document,
            Types.PHOTO.value: self.bot.client.send_photo,
            Types.VIDEO.value: self.bot.client.send_video,
            Types.STICKER.value: self.bot.client.send_sticker,
            Types.AUDIO.value: self.bot.client.send_audio,
            Types.VOICE.value: self.bot.client.send_voice,
            Types.VIDEO_NOTE.value: self.bot.client.send_video_note,
            Types.ANIMATION.value: self.bot.client.send_animation,
        }

    async def on_chat_action(self, message: Message) -> None:
        """Handle Telegram join/leave service messages safely."""
        self.log.info(
            "GREETING EVENT: chat=%s new_members=%s left_member=%s message_id=%s",
            message.chat.id,
            [u.id for u in message.new_chat_members] if message.new_chat_members else [],
            message.left_chat_member.id if message.left_chat_member else None,
            message.id,
        )

        chat = message.chat

        # Never send a goodbye when the bot itself leaves/is removed.
        if message.left_chat_member and message.left_chat_member.id == self.bot.uid:
            return

        # If clean-service is enabled, delete Telegram's join/leave service
        # message first. Do NOT use message_id=0 afterwards; simply send the
        # greeting without a reply target.
        reply_to: Optional[int] = message.id
        if await self.clean_service(chat.id):
            try:
                await message.delete()
                reply_to = None
            except (MessageDeleteForbidden, ChannelPrivate):
                # The service message still exists, so it remains a valid
                # reply target.
                reply_to = message.id

        thread_id = await self.get_action_topic(chat)
        if chat.is_forum and not thread_id:
            self.log.debug(
                "Chat %s is forum but no action topic is configured.",
                chat.id,
            )

        try:
            if message.new_chat_members:
                await self._member_join(message, reply_to, thread_id)
            elif message.left_chat_member:
                await self._member_leave(message, reply_to, thread_id)
        except ChatWriteForbidden:
            self.log.warning(
                "Cannot send greeting in chat %s: bot has no write permission.",
                chat.id,
            )
        except Exception:
            # Never silently lose a greeting. Full traceback goes to the bot log.
            self.log.exception("GREETING EVENT failed in chat %s.", chat.id)

    async def _member_leave(
        self, message: Message, reply_to: Optional[int], thread_id: Optional[int]
    ) -> None:
        chat = message.chat
        if not await self.is_goodbye(chat.id):
            return

        left_member = message.left_chat_member
        text = await self.left_message(chat.id)
        if not text:
            text = await self.text(chat.id, "default-goodbye", noformat=True)

        formatted_text = await self._build_text(text, left_member, chat, self.bot.client)
        try:
            msg = await self.bot.client.send_message(
                chat.id,
                formatted_text,
                reply_to_message_id=reply_to if not thread_id else None,  # type: ignore
                message_thread_id=thread_id,  # type: ignore
            )
        except ChatWriteForbidden:
            return

        previous = await self.previous_goodbye(chat.id, msg.id)
        if previous:
            try:
                await self.bot.client.delete_messages(chat.id, previous)
            except MessageDeleteForbidden:
                pass

    async def _send_welcome_for_member(
        self,
        chat: Chat,
        new_member: User,
        reply_to: Optional[int] = None,
        thread_id: Optional[int] = None,
    ) -> Optional[Message]:
        """Send the configured welcome to one member."""
        if not await self.is_welcome(chat.id):
            self.log.info("Welcome disabled for chat %s.", chat.id)
            return None

        if new_member.id == self.bot.uid:
            kwargs: MutableMapping[str, Any] = {}
            if reply_to is not None:
                kwargs["reply_to_message_id"] = reply_to
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id
            return await self.bot.client.send_message(
                chat.id,
                await self.text(chat.id, "bot-added"),
                **kwargs,
            )

        text, button, msg_type, file_id = await self.welc_message(chat.id)
        msg_type = Types(msg_type) if msg_type else Types.TEXT
        string = text or await self.text(chat.id, "default-welcome", noformat=True)

        formatted_text = await self._build_text(
            string, new_member, chat, self.bot.client
        )

        button = build_button(button) if button else None

        async def send_welcome(include_context: bool = True) -> Optional[Message]:
            kwargs: MutableMapping[str, Any] = {}

            if include_context:
                if thread_id is not None:
                    kwargs["message_thread_id"] = thread_id
                if reply_to is not None:
                    kwargs["reply_to_message_id"] = reply_to

            if msg_type in {Types.TEXT, Types.BUTTON_TEXT}:
                kwargs.update(
                    {
                        "reply_markup": button,
                        "disable_web_page_preview": True,
                    }
                )
                return await self.SEND[msg_type.value](
                    chat.id,
                    formatted_text,
                    **kwargs,
                )

            if msg_type in {Types.STICKER, Types.ANIMATION}:
                return await self.SEND[msg_type.value](
                    chat.id,
                    file_id,
                    **kwargs,
                )

            kwargs.update(
                {
                    "caption": formatted_text,
                    "reply_markup": button,
                }
            )
            return await self.SEND[msg_type.value](
                chat.id,
                file_id,
                **kwargs,
            )

        msg: Optional[Message] = None

        try:
            try:
                msg = await send_welcome(include_context=True)
            except (MediaEmpty, MessageEmpty):
                raise
            except Exception:
                self.log.exception(
                    "Welcome contextual send failed in chat %s; retrying normally.",
                    chat.id,
                )
                msg = await send_welcome(include_context=False)

        except MediaEmpty:
            await self.bot.client.send_message(
                chat.id,
                await self.text(chat.id, "welcome-message-expired"),
            )

        except MessageEmpty:
            self.log.warning("Welcome message is empty in chat %s.", chat.id)

        except ChatWriteForbidden:
            raise

        except Exception:
            self.log.exception(
                "Failed to send welcome in chat %s for user %s.",
                chat.id,
                new_member.id,
            )

            # Last-resort plain-text fallback.
            try:
                fallback = await self._build_text(
                    await self.text(chat.id, "default-welcome", noformat=True),
                    new_member,
                    chat,
                    self.bot.client,
                )
                msg = await self.bot.client.send_message(
                    chat.id,
                    fallback,
                    disable_web_page_preview=True,
                )
            except Exception:
                self.log.exception(
                    "Last-resort welcome failed in chat %s.",
                    chat.id,
                )

        return msg

    async def _member_join(
        self,
        message: Message,
        reply_to: Optional[int],
        thread_id: Optional[int],
    ) -> None:
        """Handle the traditional Telegram new_chat_members event."""
        chat = message.chat
        new_members = message.new_chat_members or []

        if not new_members:
            return

        is_bulk_welcome = len(new_members) > 1

        for idx, new_member in enumerate(new_members):
            try:
                msg = await self._send_welcome_for_member(
                    chat,
                    new_member,
                    reply_to=reply_to,
                    thread_id=thread_id,
                )

                if msg:
                    previous = await self.previous_welcome(
                        chat.id,
                        msg.id,
                        is_bulk_welcome,
                    )
                    if idx == 0 and previous:
                        try:
                            await self.bot.client.delete_messages(
                                chat.id,
                                previous,
                            )
                        except MessageDeleteForbidden:
                            pass

            except ChatWriteForbidden:
                self.log.warning(
                    "Cannot send greeting in chat %s: bot has no write permission.",
                    chat.id,
                )

    async def on_chat_member_update(self, update: ChatMemberUpdated) -> None:
        """
        Handle joins reported through Telegram chat-member updates.

        This covers users whose join is approved through a join request,
        where Telegram may not provide a new_chat_members service message.
        """
        if not update.chat:
            return

        old_member = update.old_chat_member
        new_member = update.new_chat_member

        if not new_member or not new_member.user:
            return

        def is_active(member: Any) -> bool:
            if member is None:
                return False

            status = member.status

            if status in {
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            }:
                return True

            if status == ChatMemberStatus.RESTRICTED:
                return bool(getattr(member, "is_member", False))

            return False

        # Only inactive -> active is a join. This prevents promotions,
        # permission changes and ordinary member edits from triggering
        # another welcome.
        if is_active(old_member) or not is_active(new_member):
            return

        self.log.info(
            "GREETING JOIN EVENT: chat=%s user=%s via_join_request=%s",
            update.chat.id,
            new_member.user.id,
            getattr(update, "via_join_request", False),
        )

        try:
            thread_id = await self.get_action_topic(update.chat)

            msg = await self._send_welcome_for_member(
                update.chat,
                new_member.user,
                reply_to=None,
                thread_id=thread_id,
            )

            if msg:
                previous = await self.previous_welcome(
                    update.chat.id,
                    msg.id,
                    False,
                )
                if previous:
                    try:
                        await self.bot.client.delete_messages(
                            update.chat.id,
                            previous,
                        )
                    except MessageDeleteForbidden:
                        pass

        except ChatWriteForbidden:
            self.log.warning(
                "Cannot send chat-member welcome in chat %s: bot has no write permission.",
                update.chat.id,
            )
        except Exception:
            self.log.exception(
                "Chat-member welcome failed in chat %s for user %s.",
                update.chat.id,
                new_member.user.id,
            )

    async def on_chat_migrate(self, message: Message) -> None:
        new_chat = message.chat.id
        old_chat = message.migrate_from_chat_id

        await self.db.update_one(
            {"chat_id": old_chat},
            {"$set": {"chat_id": new_chat}},
        )

    async def on_plugin_backup(self, chat_id: int) -> MutableMapping[str, Any]:
        welcome = await self.db.find_one({"chat_id": chat_id}, {"_id": False})
        return {self.name: welcome} if welcome else {}

    async def on_plugin_restore(self, chat_id: int, data: MutableMapping[str, Any]) -> None:
        await self.db.update_one({"chat_id": chat_id}, {"$set": data[self.name]}, upsert=True)

    @staticmethod
    async def _build_text(
        text: str, user: User, chat: Chat, client: Optional[Client] = None
    ) -> str:
        first_name = user.first_name or ""  # Ensure first name is not None
        last_name = user.last_name
        full_name = first_name + last_name if last_name else first_name
        try:
            count = await client.get_chat_members_count(chat.id) if client else "N/A"
        except ChannelPrivate:
            count = "N/A"

        username = util.tg.get_username(user)

        return text.format(
            first=escape(first_name),
            last=escape(last_name) if last_name else "",
            fullname=escape(full_name),
            username=f"@{username}" if username else user.mention,
            mention=user.mention,
            count=count,
            chatname=escape(chat.title),
            id=user.id,
        )

    async def get_action_topic(self, chat: Chat) -> Optional[int]:
        if not chat.is_forum:
            return None
        data = await self.chat_db.find_one({"chat_id": chat.id}, {"action_topic": True})
        return data.get("action_topic") if data else None

    async def is_welcome(self, chat_id: int) -> bool:
        """Get chat welcome setting"""
        active = await self.db.find_one({"chat_id": chat_id}, {"should_welcome": 1})
        return active.get("should_welcome", True) if active else True

    async def is_goodbye(self, chat_id: int) -> bool:
        """Get chat welcome setting"""
        active = await self.db.find_one({"chat_id": chat_id}, {"should_goodbye": 1})
        return active.get("should_goodbye", True) if active else True

    async def welc_message(
        self, chat_id: int
    ) -> Tuple[Optional[str], Optional[Button], Optional[int], Optional[str]]:
        """Get chat welcome string"""
        message = await self.db.find_one({"chat_id": chat_id})
        if message:
            # This checks data for old welcome schema
            # TODO: deprecate old schema on v3
            if "custom_welcome" in message:
                text: str = message["custom_welcome"]
                button: Optional[Button] = message.get("button")
                message_type: Types = Types.TEXT
                await self.db.delete_one({"chat_id": chat_id})
                await self.set_custom_welcome(
                    chat_id=chat_id,
                    text=text,
                    buttons=button,
                    message_type=message_type,
                    content=None,
                )
                self.log.info("Migrated old welcome message on %d to new schema.", chat_id)
                return text, button, message_type, None
            else:
                return (
                    message.get("text"),
                    message.get("button"),
                    message.get("type"),
                    message.get("file_id"),
                )
        return await self.text(chat_id, "default-welcome", noformat=True), None, None, None

    async def left_message(self, chat_id: int) -> str:
        message = await self.db.find_one({"chat_id": chat_id}, {"custom_goodbye": 1})
        return (
            message.get(
                "custom_goodbye", await self.text(chat_id, "default-goodbye", noformat=True)
            )
            if message
            else await self.text(chat_id, "default-goodbye", noformat=True)
        )

    async def clean_service(self, chat_id: int) -> bool:
        """Fetch clean service setting"""
        clean = await self.db.find_one({"chat_id": chat_id}, {"clean_service": 1})
        if clean:
            return clean.get("clean_service", True)

        return False  # Defaults off

    async def set_custom_welcome(
        self,
        chat_id: int,
        text: str,
        message_type: Types,
        buttons: Optional[Button] = None,
        content: Optional[str] = None,
    ) -> None:
        """Set custom welcome"""
        await self.db.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "text": text,
                    "button": buttons,
                    "file_id": content,
                    "type": message_type,
                }
            },
            upsert=True,
        )

    async def set_custom_goodbye(self, chat_id: int, text: str) -> None:
        """Set custom goodbye"""
        await self.db.update_one({"chat_id": chat_id}, {"$set": {"custom_goodbye": text}})

    async def del_custom_welcome(self, chat_id: int) -> None:
        """Delete custom welcome message"""
        await self.db.update_one(
            {"chat_id": chat_id},
            {
                "$unset": {
                    "custom_welcome": "",
                    "text": "",
                    "button": "",
                    "type": "",
                    "file_id": "",
                }
            },
        )

    async def del_custom_goodbye(self, chat_id: int) -> None:
        """Delete custom goodbye message"""
        await self.db.update_one({"chat_id": chat_id}, {"$unset": {"custom_goodbye": ""}})

    async def greeting_setting(self, chat_id: int, key: str, value: bool) -> None:
        """Turn on/off greetings in chats"""
        if not value:
            await self.db.update_one({"chat_id": chat_id}, {"$set": {key: False}}, upsert=True)
        else:
            await self.db.update_one({"chat_id": chat_id}, {"$unset": {key: ""}}, upsert=True)

    async def previous_welcome(
        self, chat_id: int, msg_id: int, is_bulk: bool = False
    ) -> Union[int, List[int], None]:
        """Save latest welcome msg_id and return previous msg_id"""
        operator = "$push" if is_bulk else "$set"
        data = await self.db.find_one_and_update(
            {"chat_id": chat_id}, {operator: {"prev_welc": msg_id}}, upsert=True
        )
        return data.get("prev_welc", None) if data else None

    async def previous_goodbye(self, chat_id: int, msg_id: int) -> Optional[int]:
        data = await self.db.find_one_and_update(
            {"chat_id": chat_id}, {"$set": {"prev_gdby": msg_id}}, upsert=True
        )
        return data.get("prev_gdby", None) if data else None

    @command.filters(filters.admin_only)
    async def cmd_setwelcome(self, ctx: command.Context) -> str:
        """Set chat welcome message"""
        chat = ctx.chat

        if ctx.input:
            if ctx.message.media:
                # TODO: Add support for command in media caption
                return await self.text(chat.id, "unsupported-media-command")
            else:
                welc_text = (
                    Str(ctx.message.text)
                    .init(ctx.msg.entities)
                    .markdown.split(ctx.invoker, 1)[1]
                    .strip()
                )
                welc_text, buttons = parse_button(welc_text)
                types = Types.TEXT
                content = None
                if ctx.msg.reply_to_message:
                    _, types, content, __ = get_message_info(ctx.msg)
        elif ctx.msg.reply_to_message:
            welc_text, types, content, buttons = get_message_info(ctx.msg)
        else:
            return await self.text(chat.id, "greetings-no-input")

        if not welc_text:
            return await self.text(chat.id, "greetings-button-only-error")

        try:  # Try to build a text first to check message validity
            await self._build_text(
                welc_text or "", ctx.author or self.bot.user, chat, self.bot.client
            )
        except (KeyError, ValueError) as e:
            return await self.text(chat.id, "err-msg-format-parsing", err=e)

        ret, _ = await asyncio.gather(
            self.text(chat.id, "cust-welcome-set"),
            self.set_custom_welcome(chat.id, welc_text, types, buttons, content),
        )
        return ret

    @command.filters(filters.admin_only)
    async def cmd_setgoodbye(self, ctx: command.Context) -> str:
        """Set chat goodbye message"""
        chat = ctx.chat
        if ctx.input:
            gby_text = Str(ctx.input).init(ctx.msg.entities[1:])
        elif ctx.msg.reply_to_message:
            gby_text = ctx.msg.reply_to_message.text or ctx.msg.reply_to_message.caption
        else:
            return await self.text(chat.id, "greetings-no-input")

        ret, _ = await asyncio.gather(
            self.text(chat.id, "cust-goodbye-set"), self.set_custom_goodbye(chat.id, gby_text)
        )
        return ret

    @command.filters(filters.admin_only)
    async def cmd_resetwelcome(self, ctx: command.Context) -> str:
        """Reset saved welcome message"""
        chat = ctx.chat

        ret, _ = await asyncio.gather(
            self.text(chat.id, "reset-welcome"), self.del_custom_welcome(chat.id)
        )
        return ret

    @command.filters(filters.admin_only)
    async def cmd_resetgoodbye(self, ctx: command.Context) -> str:
        """Reset saved welcome message"""
        chat = ctx.chat

        ret, _ = await asyncio.gather(
            self.text(chat.id, "reset-goodbye"), self.del_custom_goodbye(chat.id)
        )
        return ret

    @command.filters(filters.admin_only)
    async def cmd_welcome(self, ctx: command.Context) -> Optional[str]:
        """View current welcome message"""
        chat = ctx.chat
        param = ctx.input.lower()
        noformat = param == "noformat"

        enabled = None
        if param in {"yes", "on", "1"}:
            enabled = True
        elif param in {"no", "off", "0"}:
            enabled = False
        elif param and not noformat:
            return await self.text(chat.id, "err-invalid-option")

        if enabled is not None:
            ret, _ = await asyncio.gather(
                self.text(chat.id, "welcome-set", "on" if enabled else "off"),
                self.greeting_setting(chat.id, "should_welcome", enabled),
            )
            return ret

        (
            setting,
            (text, button, msg_type, file_id),
            clean_service,
        ) = await asyncio.gather(
            self.is_welcome(chat.id), self.welc_message(chat.id), self.clean_service(chat.id)
        )

        if text is None:
            text = ""
        else:
            text += "\n\n"

        if noformat:
            parse_mode = ParseMode.DISABLED
            if button:
                text += revert_button(button)
            button = None
        else:
            parse_mode = ParseMode.MARKDOWN
            if button:
                button = build_button(button)

        view_welc = await self.text(chat.id, "view-welcome", setting, clean_service)
        if ctx.chat.is_forum and not await self.get_action_topic(ctx.chat):
            view_welc += "\n\n" + await self.text(
                chat.id,
                "greetings-topic-default",
                f"https://t.me/{self.bot.user.username}?start=help_topic",
            )

        settings_msg = await ctx.respond(view_welc)

        reply_to = settings_msg.id if settings_msg else None
        try:
            response_text = (
                text
                if text
                else (
                    "Empty, custom welcome message haven't set yet."
                    if not setting
                    else "Default:\n\n" + await self.text(chat.id, "default-welcome", noformat=True)
                )
            )
            msg_type = msg_type or Types.TEXT
            if msg_type in {Types.TEXT, Types.BUTTON_TEXT}:
                await self.SEND[msg_type.value](
                    ctx.chat.id,
                    response_text,
                    reply_to_message_id=reply_to,
                    reply_markup=button,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
            elif msg_type in {Types.STICKER, Types.ANIMATION}:
                await self.SEND[msg_type.value](
                    ctx.chat.id,
                    file_id,
                    reply_to_message_id=reply_to,
                )
            else:
                await self.SEND[msg_type.value](
                    ctx.chat.id,
                    file_id,
                    caption=text,
                    reply_to_message_id=reply_to,
                    parse_mode=parse_mode,
                    reply_markup=button,
                )
        except MediaEmpty:
            await self.bot.client.send_message(
                ctx.chat.id, await self.text(ctx.chat.id, "welcome-message-expired")
            )
        except MessageEmpty:
            self.log.warning("Welcome message empty on %s.", ctx.chat.id)

    @command.filters(filters.admin_only)
    async def cmd_goodbye(self, ctx: command.Context) -> Optional[str]:
        """View current goodbye message"""
        chat = ctx.chat
        param = ctx.input.lower()
        noformat = param == "noformat"

        enabled = None
        if param in {"yes", "on", "1"}:
            enabled = True
        elif param in {"no", "off", "0"}:
            enabled = False
        elif param and not noformat:
            return await self.text(chat.id, "err-invalid-option")

        if enabled is not None:
            ret, _ = await asyncio.gather(
                self.text(chat.id, "goodbye-set", "on" if enabled else "off"),
                self.greeting_setting(chat.id, "should_goodbye", enabled),
            )
            return ret

        setting, text, clean_service = await asyncio.gather(
            self.is_goodbye(chat.id), self.left_message(chat.id), self.clean_service(chat.id)
        )

        if noformat:
            parse_mode = ParseMode.DISABLED
        else:
            parse_mode = ParseMode.MARKDOWN

        view_gby = await self.text(chat.id, "view-goodbye", setting, clean_service)
        if ctx.chat.is_forum and not await self.get_action_topic(ctx.chat):
            view_gby += "\n\n" + await self.text(
                chat.id,
                "greetings-topic-default",
                f"https://t.me/{self.bot.user.username}?start=help_topic",
            )

        await ctx.respond(view_gby)
        await ctx.respond(
            text,
            mode="reply",
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        return None

    @command.filters(filters.admin_only)
    async def cmd_cleanservice(self, ctx: command.Context, active: Optional[bool] = None) -> str:
        """Clean service message on new members"""
        chat = ctx.chat

        if active is None:
            return await self.text(chat.id, "err-invalid-option")

        ret, _ = await asyncio.gather(
            self.text(chat.id, "clean-serv-set", "on" if active else "off"),
            self.greeting_setting(chat.id, "clean_service", active),
        )
        return ret
