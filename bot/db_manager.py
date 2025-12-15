import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload


class Base(DeclarativeBase):
    pass


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[Optional[str]] = mapped_column(String(255))
    fio: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_info: Mapped[str] = mapped_column(Text, nullable=False)
    gift_wishes: Mapped[Optional[str]] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    gives_to: Mapped[List["DrawResult"]] = relationship(
        "DrawResult", back_populates="giver", foreign_keys="DrawResult.giver_id"
    )
    receives_from: Mapped[List["DrawResult"]] = relationship(
        "DrawResult", back_populates="receiver", foreign_keys="DrawResult.receiver_id"
    )


class DrawResult(Base):
    __tablename__ = "draw_results"
    __table_args__ = (UniqueConstraint("giver_id", "receiver_id", name="uq_giver_receiver"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giver_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), nullable=False)
    receiver_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), nullable=False)
    draw_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    giver: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[giver_id], back_populates="gives_to"
    )
    receiver: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[receiver_id], back_populates="receives_from"
    )

class DeliveryMessage(Base):
    __tablename__ = "delivery_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giver_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), nullable=False)
    receiver_id: Mapped[int] = mapped_column(ForeignKey("participants.id"), nullable=False)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # text|photo|document|unknown
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="sent")  # sent|failed

    text: Mapped[Optional[str]] = mapped_column(Text)
    caption: Mapped[Optional[str]] = mapped_column(Text)
    telegram_file_id: Mapped[Optional[str]] = mapped_column(String(512))
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    giver: Mapped[Participant] = relationship("Participant", foreign_keys=[giver_id])
    receiver: Mapped[Participant] = relationship("Participant", foreign_keys=[receiver_id])


class Database:
    def __init__(self, *, user: str, password: str, host: str, db_name: str) -> None:
        self._engine = create_async_engine(
            f"mysql+asyncmy://{user}:{password}@{host}/{db_name}", future=True, echo=False
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_models(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> Iterable[AsyncSession]:
        async with self._session_factory() as session:
            yield session

    async def upsert_participant(
        self,
        *,
        telegram_id: int,
        username: Optional[str],
        first_name: str,
        last_name: Optional[str],
        fio: str,
        delivery_info: str,
        gift_wishes: Optional[str],
        is_admin: bool,
    ) -> Participant:
        async with self.session() as session:
            participant = await session.scalar(
                select(Participant).where(Participant.telegram_id == telegram_id)
            )
            if participant:
                participant.fio = fio
                participant.delivery_info = delivery_info
                participant.gift_wishes = gift_wishes
                participant.username = username
                participant.first_name = first_name
                participant.last_name = last_name
                participant.is_admin = is_admin
            else:
                participant = Participant(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    fio=fio,
                    delivery_info=delivery_info,
                    gift_wishes=gift_wishes,
                    is_admin=is_admin,
                )
                session.add(participant)
            await session.commit()
            await session.refresh(participant)
            return participant

    async def get_participants(self) -> List[Participant]:
        async with self.session() as session:
            result = await session.scalars(select(Participant))
            return list(result)

    async def get_participant_by_telegram_id(self, telegram_id: int) -> Optional[Participant]:
        async with self.session() as session:
            return await session.scalar(select(Participant).where(Participant.telegram_id == telegram_id))

    async def delete_participant_by_telegram_id(self, telegram_id: int) -> bool:
        async with self.session() as session:
            participant = await session.scalar(select(Participant).where(Participant.telegram_id == telegram_id))
            if not participant:
                return False
            await session.delete(participant)
            await session.commit()
            return True

    async def store_draw(self, pairs: List[tuple[int, int]]) -> None:
        now = datetime.utcnow()
        async with self.session() as session:
            for giver_id, receiver_id in pairs:
                session.add(
                    DrawResult(giver_id=giver_id, receiver_id=receiver_id, draw_time=now)
                )
            await session.commit()

    async def clear_draw_results(self) -> None:
        async with self.session() as session:
            await session.execute(delete(DrawResult))
            await session.commit()

    async def get_pairs(self) -> List[DrawResult]:
        async with self.session() as session:
            result = await session.scalars(
                select(DrawResult)
                .options(selectinload(DrawResult.giver), selectinload(DrawResult.receiver))
                .join(Participant, Participant.id == DrawResult.giver_id)
                .order_by(DrawResult.id)
            )
            return list(result)

    async def get_receiver_for_giver(self, telegram_id: int) -> Optional[DrawResult]:
        async with self.session() as session:
            return await session.scalar(
                select(DrawResult)
                .options(selectinload(DrawResult.receiver), selectinload(DrawResult.giver))
                .join(Participant, Participant.id == DrawResult.giver_id)
                .where(Participant.telegram_id == telegram_id)
            )

    async def log_delivery_message(
        self,
        *,
        giver_telegram_id: int,
        receiver_telegram_id: int,
        kind: str,
        status: str,
        text: Optional[str] = None,
        caption: Optional[str] = None,
        telegram_file_id: Optional[str] = None,
        telegram_message_id: Optional[int] = None,
        error: Optional[str] = None,
    ) -> DeliveryMessage:
        async with self.session() as session:
            giver = await session.scalar(select(Participant).where(Participant.telegram_id == giver_telegram_id))
            receiver = await session.scalar(
                select(Participant).where(Participant.telegram_id == receiver_telegram_id)
            )
            if not giver or not receiver:
                raise ValueError("giver or receiver not found")

            row = DeliveryMessage(
                giver_id=giver.id,
                receiver_id=receiver.id,
                kind=kind,
                status=status,
                text=text,
                caption=caption,
                telegram_file_id=telegram_file_id,
                telegram_message_id=telegram_message_id,
                error=error,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_delivery_messages(self, *, limit: int = 50) -> List[DeliveryMessage]:
        async with self.session() as session:
            result = await session.scalars(
                select(DeliveryMessage)
                .options(selectinload(DeliveryMessage.giver), selectinload(DeliveryMessage.receiver))
                .order_by(DeliveryMessage.id.desc())
                .limit(limit)
            )
            return list(result)

    async def get_delivery_messages_for_user(self, *, telegram_id: int, limit: int = 50) -> List[DeliveryMessage]:
        async with self.session() as session:
            participant = await session.scalar(select(Participant).where(Participant.telegram_id == telegram_id))
            if not participant:
                return []
            result = await session.scalars(
                select(DeliveryMessage)
                .options(selectinload(DeliveryMessage.giver), selectinload(DeliveryMessage.receiver))
                .where((DeliveryMessage.giver_id == participant.id) | (DeliveryMessage.receiver_id == participant.id))
                .order_by(DeliveryMessage.id.desc())
                .limit(limit)
            )
            return list(result)


async def init_database(db: Database) -> None:
    await db.init_models()


def run_migrations(db: Database) -> None:
    asyncio.get_event_loop().run_until_complete(init_database(db))
