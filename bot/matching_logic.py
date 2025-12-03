import random
from typing import List, Sequence, Tuple

from .db_manager import Participant


class DrawError(Exception):
    pass


def generate_derangement(participants: Sequence[Participant]) -> List[Tuple[int, int]]:
    participant_ids = [p.id for p in participants]
    receivers = participant_ids.copy()
    attempts = 0
    while attempts < 50:
        random.shuffle(receivers)
        if all(giver != receiver for giver, receiver in zip(participant_ids, receivers)):
            return list(zip(participant_ids, receivers))
        attempts += 1
    raise DrawError("Не удалось подобрать распределение без самодарения")
