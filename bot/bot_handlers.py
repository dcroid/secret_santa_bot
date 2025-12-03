import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from .config import Settings
from .db_manager import Database
from .matching_logic import DrawError, generate_derangement


RECIPIENT_BTN = "Мой получатель 🥰"
BUDGET_BTN = "Бюджет подарка 💴🎁"


class RegistrationForm(StatesGroup):
    fio = State()
    delivery = State()
    wishes = State()


def is_admin(message: Message, settings: Settings) -> bool:
    return message.from_user and message.from_user.id == settings.admin_id


def user_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=RECIPIENT_BTN), KeyboardButton(text=BUDGET_BTN)],
        ],
        resize_keyboard=True,
    )


def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/admin_menu"), KeyboardButton(text="/list_participants")],
            [KeyboardButton(text="/start_draw"), KeyboardButton(text="/restart_draw")],
            [KeyboardButton(text="/view_pairs"), KeyboardButton(text="/delete_participant")],
        ],
        resize_keyboard=True,
    )


def setup_handlers(dp: Dispatcher, db: Database, settings: Settings) -> None:
    async def ensure_admin(message: Message) -> bool:
        if not is_admin(message, settings):
            await message.answer("Команда доступна только администратору.")
            return False
        return True

    @dp.message(Command("start", "register"))
    async def register(message: Message, state: FSMContext) -> None:
        await state.clear()
        existing = await db.get_participant_by_telegram_id(message.from_user.id)
        if existing:
            await message.answer(
                "Ты уже зарегистрирован в Тайном Санте.\n"
                f"ФИО: {existing.fio}\n"
                f"Адрес доставки: {existing.delivery_info}\n"
                f"Пожелания: {existing.gift_wishes or 'не указаны'}\n"
                f"Бюджет подарка: {settings.budget}\n"
                "Если нужно обновить данные — напиши админу."
            )
            return
        await message.answer(
            "Привет! Давай зарегистрируем тебя для Тайного Санты. Введи, пожалуйста, своё ФИО.",
            reply_markup=user_keyboard(),
        )
        await state.set_state(RegistrationForm.fio)

    @dp.message(Command("start_menu"))
    async def start_menu(message: Message) -> None:
        await message.answer(
            "Главное меню. Используй кнопки ниже.",
            reply_markup=user_keyboard(),
            )

    @dp.message(RegistrationForm.fio)
    async def process_fio(message: Message, state: FSMContext) -> None:
        await state.update_data(fio=message.text.strip())
        await message.answer("Спасибо! Укажи способ и адрес доставки.")
        await state.set_state(RegistrationForm.delivery)

    @dp.message(RegistrationForm.delivery)
    async def process_delivery(message: Message, state: FSMContext) -> None:
        await state.update_data(delivery=message.text.strip())
        await message.answer(
            "Отлично! Теперь можешь написать пожелания к подарку (или отправь '-' если нет)."
        )
        await state.set_state(RegistrationForm.wishes)

    @dp.message(RegistrationForm.wishes)
    async def process_wishes(message: Message, state: FSMContext) -> None:
        wishes = None if message.text.strip() == "-" else message.text.strip()
        data = await state.get_data()
        user = message.from_user
        await db.upsert_participant(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            fio=data["fio"],
            delivery_info=data["delivery"],
            gift_wishes=wishes,
            is_admin=is_admin(message, settings),
        )
        await state.clear()
        await message.answer(
            "Регистрация завершена!\n"
            f"ФИО: {data['fio']}\n"
            f"Адрес доставки: {data['delivery']}\n"
            f"Пожелания: {wishes or 'не указаны'}\n"
            f"Бюджет подарка: {settings.budget}\n"
            "Жеребьевка пока не проведена. Ожидайте уведомления!"
        )

    @dp.message(Command("admin_menu"))
    async def admin_menu(message: Message) -> None:
        if not await ensure_admin(message):
            return
        await message.answer(
            "Админ-меню:\n"
            "/list_participants — список участников\n"
            "/start_draw — запустить жеребьевку\n"
            "/restart_draw — перезапуск жеребьевки\n"
            "/view_pairs — показать текущие пары",
            reply_markup=admin_keyboard(),
        )

    @dp.message(Command("list_participants"))
    async def list_participants(message: Message) -> None:
        if not await ensure_admin(message):
            return
        participants = await db.get_participants()
        if not participants:
            await message.answer("Участников пока нет.")
            return
        lines = []
        for p in participants:
            status = "полная" if p.fio and p.delivery_info else "неполная"
            lines.append(
                f"ФИО: {p.fio}\nTG ID: {p.telegram_id}\nUsername: {p.username or '-'}\nСтатус: {status}\n"
            )
        await message.answer("\n".join(lines))

    async def notify_pairs(bot: Bot, pairs):
        sent = 0
        failed = 0
        failures = []
        for draw in pairs:
            giver = draw.giver
            receiver = draw.receiver
            text = (
                "Поздравляем! Жеребьевка состоялась.\n"
                f"Твой получатель: {receiver.fio}.\n"
                f"Адрес доставки: {receiver.delivery_info}.\n"
                f"Пожелания: {receiver.gift_wishes or 'не указаны'}.\n"
                f"Бюджет подарка: {settings.budget}."
            )
            try:
                await bot.send_message(chat_id=giver.telegram_id, text=text)
                sent += 1
            except Exception as exc:
                failed += 1
                failures.append((giver.telegram_id, str(exc)))
                logging.exception("Не удалось отправить уведомление участнику %s", giver.telegram_id)
        return sent, failed, failures

    @dp.message(Command("start_draw"))
    async def start_draw(message: Message) -> None:
        if not await ensure_admin(message):
            return
        participants = await db.get_participants()
        if len(participants) < 2:
            await message.answer("Недостаточно участников для жеребьевки (нужно минимум 2).")
            return
        try:
            pairs = generate_derangement(participants)
        except DrawError as exc:
            await message.answer(str(exc))
            return
        await db.clear_draw_results()
        await db.store_draw(pairs)
        draw_rows = await db.get_pairs()
        await message.answer("Жеребьевка проведена! Отправляю уведомления участникам...")
        sent, failed, failures = await notify_pairs(message.bot, draw_rows)
        summary = f"Уведомления отправлены: {sent} усп., {failed} с ошибкой."
        if failures:
            details = "\n".join(f"TG {tg_id}: {err}" for tg_id, err in failures)
            summary += f"\nОшибки доставки:\n{details}"
        await message.answer(summary, reply_markup=admin_keyboard())

    @dp.message(Command("restart_draw"))
    async def restart_draw(message: Message) -> None:
        if not await ensure_admin(message):
            return
        await db.clear_draw_results()
        await message.answer("Результаты очищены. Запускаю новую жеребьевку...")
        await start_draw(message)

    @dp.message(Command("view_pairs"))
    async def view_pairs(message: Message) -> None:
        if not await ensure_admin(message):
            return
        pairs = await db.get_pairs()
        if not pairs:
            await message.answer("Пары не найдены. Проведите жеребьевку.")
            return
        lines = []
        for pair in pairs:
            lines.append(
                f"Даритель: {pair.giver.fio} (TG {pair.giver.telegram_id}) -> "
                f"Получатель: {pair.receiver.fio} (TG {pair.receiver.telegram_id})"
            )
        await message.answer("\n".join(lines))

    @dp.message(Command("delete_participant"))
    async def delete_participant(message: Message) -> None:
        if not await ensure_admin(message):
            return
        parts = message.text.split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            await message.answer("Использование: /delete_participant <telegram_id>")
            return
        tg_id = int(parts[1].strip())
        deleted = await db.delete_participant_by_telegram_id(tg_id)
        if deleted:
            await message.answer(f"Пользователь с TG ID {tg_id} удалён.")
        else:
            await message.answer("Пользователь с таким TG ID не найден.")

    @dp.message(Command("budget"))
    @dp.message(F.text == BUDGET_BTN)
    async def budget(message: Message) -> None:
        await message.answer(f"Бюджет подарка: {settings.budget}")

    @dp.message(Command("my_recipient"))
    @dp.message(F.text == RECIPIENT_BTN)
    async def my_recipient(message: Message) -> None:
        participant = await db.get_participant_by_telegram_id(message.from_user.id)
        if not participant:
            await message.answer("Ты ещё не зарегистрирован. Нажми /start и пройди регистрацию.")
            return
        draw = await db.get_receiver_for_giver(message.from_user.id)
        if not draw:
            await message.answer("Жеребьёвка пока не проведена или пары не назначены.")
            return
        receiver = draw.receiver
        text = (
            "Твоя пара Тайного Санты:\n"
            f"ФИО: {receiver.fio}\n"
            f"Адрес доставки: {receiver.delivery_info}\n"
            f"Пожелания: {receiver.gift_wishes or 'не указаны'}\n"
            f"Бюджет подарка: {settings.budget}"
        )
        await message.answer(text, reply_markup=user_keyboard())

    @dp.message(F.text)
    async def fallback(message: Message) -> None:
        await message.answer(
            "Неизвестная команда. Используйте /start для регистрации.",
            reply_markup=user_keyboard(),
        )
