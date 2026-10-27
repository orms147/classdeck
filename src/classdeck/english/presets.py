"""Quick edit instructions shown as one-click buttons in the editor.

Free-text instructions are always allowed; a preset is just a well-formed one. Presets that
take a number keep a ``{{n}}`` placeholder in ``instruction`` for the editor to fill.
"""

from __future__ import annotations

from classdeck.lesson.schema import BlockKind, Strict

_NUMBER_PLACEHOLDER = "{{n}}"


class EditPreset(Strict):
    id: str
    label_en: str
    label_vi: str
    instruction: str
    applies_to: list[BlockKind]

    @property
    def takes_number(self) -> bool:
        return _NUMBER_PLACEHOLDER in self.instruction

    def render(self, n: int | None = None) -> str:
        """The instruction with ``{{n}}`` replaced; ``n`` is required when the preset takes one."""
        if not self.takes_number:
            return self.instruction
        if n is None:
            raise ValueError(f"preset {self.id!r} needs a number")
        return self.instruction.replace(_NUMBER_PLACEHOLDER, str(n))


_TEACHING_KINDS = [
    BlockKind.VOCABULARY,
    BlockKind.SENTENCE_PATTERN,
    BlockKind.DIALOGUE,
    BlockKind.PHONICS,
    BlockKind.ACTIVITY,
]

EDIT_PRESETS: list[EditPreset] = [
    EditPreset(
        id="pair_work",
        label_en="Make it a pair-work activity",
        label_vi="Chuyển thành hoạt động theo cặp",
        instruction=(
            "Turn this activity into pair work. Keep the target language and the skills. "
            "Rewrite the teacher and pupil steps for pairs."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
    EditPreset(
        id="whole_class_game",
        label_en="Make it a whole-class game",
        label_vi="Chuyển thành trò chơi cả lớp",
        instruction=(
            "Turn this activity into a whole-class game. Keep the target language and the "
            "skills. Give clear rules and short steps."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
    EditPreset(
        id="add_listening",
        label_en="Add a listening task",
        label_vi="Thêm nhiệm vụ nghe",
        instruction=(
            "Add a short listening task to this activity. Add listening to its skills and "
            "add a teacher step and a pupil step for it."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
    EditPreset(
        id="reduce_words",
        label_en="Reduce new words to N",
        label_vi="Giảm số từ mới xuống N",
        instruction=(
            "Reduce the new words to {{n}}. Keep the words that the target pattern needs "
            "and the words that are easiest to picture."
        ),
        applies_to=[BlockKind.VOCABULARY],
    ),
    EditPreset(
        id="simpler_instructions",
        label_en="Simpler instructions for pupils",
        label_vi="Đơn giản hóa hướng dẫn cho học sinh",
        instruction=(
            "Rewrite the instructions for pupils in simpler English: at most 12 words each, "
            "one action each, written as imperatives."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
    EditPreset(
        id="add_tpr",
        label_en="Add a TPR (total physical response) step",
        label_vi="Thêm bước TPR (phản xạ toàn thân)",
        instruction=(
            "Add a TPR step where pupils show the meaning with actions or movement. Add a "
            "teacher step and a pupil step for it."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
    EditPreset(
        id="bilingual_notes",
        label_en="Add bilingual teacher notes",
        label_vi="Thêm ghi chú song ngữ cho giáo viên",
        instruction=(
            "Add a teacher note in English and Vietnamese with tips for teaching this block. "
            "Do not change the English content that pupils see."
        ),
        applies_to=[BlockKind.TEACHER_NOTE, *_TEACHING_KINDS],
    ),
    EditPreset(
        id="shorten_minutes",
        label_en="Shorten to N minutes",
        label_vi="Rút ngắn còn N phút",
        instruction=(
            "Shorten this activity to {{n}} minutes. Cut steps rather than rushing them and "
            "set its minutes to {{n}}."
        ),
        applies_to=[BlockKind.ACTIVITY],
    ),
]


def presets_for(kind: BlockKind) -> list[EditPreset]:
    """The presets that make sense for a block of this kind, in display order."""
    return [preset for preset in EDIT_PRESETS if kind in preset.applies_to]
