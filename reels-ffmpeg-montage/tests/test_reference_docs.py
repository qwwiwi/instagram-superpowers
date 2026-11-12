"""Guards on the numbers and commands written in the stage 2 reference documents.

They are cheap text checks that keep the documents consistent with the scripts that verify the result:
a copied command must produce a file that ``verify.py`` accepts, and the palette must meet rule 6.5.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

REFERENCES = Path(__file__).resolve().parent.parent / "references"
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two ``#RRGGBB`` colours."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _shell_commands(text: str) -> list[list[str]]:
    """Commands of the fenced ``bash`` blocks, continuation lines joined."""
    commands: list[list[str]] = []
    for block in re.findall(r"```bash\n(.*?)```", text, flags=re.S):
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "<<" in line:
                continue
            try:
                commands.append(shlex.split(line))
            except ValueError:
                continue
    return commands


def test_every_ffmpeg_command_that_writes_the_final_reel_sets_faststart() -> None:
    # verify.py rejects an mp4 whose moov atom comes after mdat; a copied skeleton must pass it.
    text = (REFERENCES / "layout-patterns.md").read_text(encoding="utf-8")
    final = [c for c in _shell_commands(text) if c and c[0] == "ffmpeg" and c[-1] == "reel.mp4"]
    assert final, "no ffmpeg command writes reel.mp4 in layout-patterns.md"
    for command in final:
        assert "-movflags" in command and command[command.index("-movflags") + 1] == "+faststart", " ".join(command)


def test_palette_text_colours_reach_4_5_on_their_backgrounds() -> None:
    text = (REFERENCES / "montage-reglament.md").read_text(encoding="utf-8")
    section = text.split("**6.1. Палитра по умолчанию**", 1)[1].split("**6.2.", 1)[0]
    rows = {cells[0]: cells[1] for line in section.splitlines()
            if line.startswith("|") and len(cells := [c.strip() for c in line.strip("|").split("|")]) >= 2}

    def first_hex(role: str) -> str:
        return HEX.findall(next(v for k, v in rows.items() if k.startswith(role)))[0]

    backgrounds = {
        "тёмном": [first_hex("Фон тёмный"), first_hex("Карточка тёмная")],
        "акцентном": [first_hex("Фон акцентный")],
        "светлом": [first_hex("Фон светлый"), first_hex("Карточка светлая")],
    }
    checked = 0
    for label, value in rows.items():
        if not label.startswith("Текст на"):
            continue
        fills = [bg for key, bgs in backgrounds.items() if key in label for bg in bgs]
        assert fills, f"unknown background in the palette row {label!r}"
        for color in HEX.findall(re.sub(r"\([^)]*\)", "", value)):  # notes in brackets are not colours
            for bg in fills:
                assert contrast(color, bg) >= 4.5, f"{color} on {bg} in {label!r}: {contrast(color, bg):.2f}"
                checked += 1
    assert checked >= 6


def _read(name: str) -> str:
    return (REFERENCES / name).read_text(encoding="utf-8")


def test_reglament_no_longer_allows_empty_slots_or_placeholders() -> None:
    text = _read("montage-reglament.md")
    for allowed in ("слот считается содержимым", "серые строки-заглушки допустимы", "пустыми слотами",
                    "каждой будущей строке стоит приглашение", "остальное – серые заглушки"):
        assert allowed not in text.lower(), allowed
    assert "**4.9. Пустых заготовок нет – никогда.**" in text
    assert "шов срезает волосы" not in text, "the close-up branch must scale the speaker down, not cut the head"


def test_acceptance_has_a_placeholder_fail_row_and_a_frame_zero_check() -> None:
    text = _read("montage-acceptance.md")
    assert re.search(r"^\| 4\.9 \| заготовки \|.*заготовка на экране = провал", text, flags=re.M)
    assert re.search(r"^\| 3\.7 \| кадр 0,00: заголовок-хук \|.*≥ 72 px.*≤ 0,30 с", text, flags=re.M)
    assert "слоты будущих пунктов – содержимое" not in text


def test_acceptance_checks_the_whole_head_with_numbers() -> None:
    row = next(line for line in _read("montage-acceptance.md").splitlines() if line.startswith("| 1.3 |"))
    assert "y ≥ 1000" in row and "y ≤ 1520" in row and "--y0" in row, row


def test_acceptance_checks_every_detail_rule_of_section_13() -> None:
    text = _read("montage-acceptance.md")
    for n in range(1, 11):
        assert re.search(rf"^\| 13\.{n} \|", text, flags=re.M), f"no acceptance row for rule 13.{n}"
    reglament = _read("montage-reglament.md")
    for n, tag in enumerate(("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10"), start=1):
        assert re.search(rf"\*\*13\.{n}\. [^*]*\({tag}\)\.\*\*", reglament), f"rule 13.{n} ({tag}) is missing"


def test_storyboard_and_skeletons_carry_no_placeholders() -> None:
    for name in ("storyboard-template.md", "layout-patterns.md"):
        text = _read(name).lower()
        for word in ("слот", "заглушк", "пунктирная рамка", "подсказкой «добавьте"):
            assert word not in text, f"{word!r} in {name}"
    assert "Кадр 0,00: заголовок-хук" in _read("storyboard-template.md")


# --- review 2: the regulation, the acceptance checklist and the skeletons agree ----------------------------------------


def _row(text: str, number: str) -> str:
    rows = [line for line in text.splitlines() if line.startswith(f"| {number} |")]
    assert rows, f"no row {number}"
    return "\n".join(rows)


def test_speaker_after_a_reduced_split_is_not_a_fail_in_acceptance() -> None:
    # Regulation 1.4: after a split at scale < 1 the SPEAKER plan runs at scale 1; acceptance 1.4 must allow it.
    acceptance = (REFERENCES / "montage-acceptance.md").read_text(encoding="utf-8")
    row = _row(acceptance, "1.4")
    assert "масштабе 1" in row and "не провал" in row


def test_hook_title_may_repeat_the_caption_in_acceptance() -> None:
    reglament = (REFERENCES / "montage-reglament.md").read_text(encoding="utf-8")
    assert "на него не действует" in reglament.split("**3.7.", 1)[1].split("**3.8", 1)[0]
    row = _row((REFERENCES / "montage-acceptance.md").read_text(encoding="utf-8"), "8.3")
    assert "кроме заголовка-хука" in row


def test_two_line_hook_zone_is_the_same_everywhere() -> None:
    # Two lines of 90 px from y 150 end at y 330: every document must allow the hook down to y 330.
    reglament = (REFERENCES / "montage-reglament.md").read_text(encoding="utf-8")
    rule_8_1 = reglament.split("**8.1.", 1)[1].split("**8.2.", 1)[0]
    rule_3_7 = reglament.split("**3.7.", 1)[1].split("**3.8", 1)[0]
    acceptance_8 = _row((REFERENCES / "montage-acceptance.md").read_text(encoding="utf-8"), "8.1–8.2")
    patterns = (REFERENCES / "layout-patterns.md").read_text(encoding="utf-8")
    for where, text in (("reglament 3.7", rule_3_7), ("reglament 8.1", rule_8_1), ("acceptance 8.1", acceptance_8)):
        assert "y 140–330" in text, where
    assert "two lines end at y 330" in patterns


ROTATION_DEG = 6.0  # rule 13.5: accents tilt -6...+6 degrees
SAFE_TOP, SAFE_RIGHT = 140, 960


def test_edge_badges_of_the_skeletons_stay_in_the_safe_zone() -> None:
    import math

    text = (REFERENCES / "layout-patterns.md").read_text(encoding="utf-8")
    css = re.search(r"\.edge \{[^}]*top: (-?\d+)px; right: (-?\d+)px", text)
    assert css, ".edge rule not found"
    edge_top, edge_right = int(css.group(1)), int(css.group(2))
    cards = list(re.finditer(r'<div class="card[^"]*"[^>]*style="left:(\d+)px; top:(\d+)px; width:(\d+)px', text))
    checked = 0
    for badge in re.finditer(r'<div class="pill edge"[^>]*>([^<]+)</div>', text):
        card = [c for c in cards if c.start() < badge.start()][-1]
        left, top, width = (int(v) for v in card.groups())
        # pill: 32 px caps with letter spacing, 26 px padding, 4 px border; 60 px high
        pill_w = len(badge.group(1)) * 32 * 0.68 + 2 * 26 + 8
        tilt = math.sin(math.radians(ROTATION_DEG)) * pill_w / 2
        y_top = top + edge_top - tilt
        x_right = left + width - edge_right + math.sin(math.radians(ROTATION_DEG)) * 34
        assert y_top >= SAFE_TOP, f"badge «{badge.group(1)}» reaches y {y_top:.0f} (card top {top})"
        assert x_right <= SAFE_RIGHT, f"badge «{badge.group(1)}» reaches x {x_right:.0f}"
        checked += 1
    assert checked >= 3


# --- the skill flow: model, fresh acceptance, publication as step 4 ----------------------------------------------------

SKILL = REFERENCES.parent / "SKILL.md"
README = REFERENCES.parent.parent / "README.md"


def test_publication_is_step_4_of_the_main_flow() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    flow = skill.split("## Общий порядок: четыре шага", 1)[1].split("\n## ", 1)[0]
    steps = re.findall(r"^(\d)\. \*\*([^*]+)\*\*", flow, flags=re.M)
    assert [n for n, _ in steps] == ["1", "2", "3", "4"] and steps[3][1] == "Публикация", steps
    assert "## Шаг 4. Публикация" in skill
    readme = README.read_text(encoding="utf-8")
    assert "**4. Публикация**" in readme


def test_publication_rules_are_safe_and_generic() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    section = skill.split("## Шаг 4. Публикация", 1)[1].split("\n## ", 1)[0]
    for must in ("явного слова", "тестовый", "API", "macOS", "кликами"):
        assert must in section, must


def test_model_rule_and_fresh_acceptance_are_written() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    model = skill.split("## Модель", 1)[1].split("\n## ", 1)[0]
    assert "Opus 5.5" in model and "medium" in model and "Sonnet" in model
    acceptance = _read("montage-acceptance.md")
    assert "свежим контекстом" in acceptance and "Самоприёмка сборщика не считается" in acceptance
    assert "Самоприёмка сборщика не считается" in skill


def test_every_acceptance_fail_class_has_a_rule_and_a_checklist_row() -> None:
    reglament = _read("montage-reglament.md")
    acceptance = _read("montage-acceptance.md")
    for n in range(1, 12):
        assert f"**14.{n}." in reglament, f"rule 14.{n} missing"
        assert re.search(rf"^\| 14\.{n} \|", acceptance, flags=re.M), f"no checklist row 14.{n}"
        assert re.search(rf"^\| 14\.{n} ", acceptance.split("## 6.", 1)[1], flags=re.M), f"no table row 14.{n}"
    assert "frame_check.py frames" in acceptance and "frame_check.py text" in acceptance


# --- review 3: one number, one boundary, one rule in every document ------------------------------------------------


def _all_docs() -> dict[str, str]:
    docs = {p.name: p.read_text(encoding="utf-8") for p in REFERENCES.glob("*.md")}
    docs["SKILL.md"] = SKILL.read_text(encoding="utf-8")
    return docs


def test_one_top_boundary_y_140_in_documents_and_scripts() -> None:
    import frame_check
    import frames_sheet
    import head_guide

    for name, text in _all_docs().items():
        assert not re.search(r"y 115|0–115|115–", text), f"old top boundary y 115 in {name}"
    assert frame_check.TOP_ZONE == frames_sheet.GUIDE_ROWS[0] == head_guide.ZONE_ROWS[0] == head_guide.TOP_SAFE == 140


def test_edge_air_is_one_number_with_one_comparison() -> None:
    import plan_cut
    import verify

    assert plan_cut.EDGE_LIMIT == verify.DEFAULT_MAX_LEAD == verify.DEFAULT_MAX_TRAIL == 0.30
    rule = _read("montage-reglament.md").split("**3.7.", 1)[1].split("**4.", 1)[0]
    assert "не больше 0,30 с" in rule and "воздух ≤ 0,30 с" in rule
    for name, text in _all_docs().items():
        assert "0,10 с" not in text, f"old edge limit 0,10 s in {name}"
        assert "шума комнаты" not in text or name == "montage-rules.md", f"noise-floor edge threshold in {name}"


def test_acceptance_splits_visual_review_and_technical_check() -> None:
    text = _read("montage-acceptance.md")
    roles = text.split("**Кто принимает", 1)[1].split("## 1.", 1)[0]
    visual = next(line for line in roles.splitlines() if line.startswith("| Визуальная приёмка"))
    technical = next(line for line in roles.splitlines() if line.startswith("| Техническая проверка"))
    assert "транскрипт" in visual and "раскадровки" in visual and "нет" in visual
    assert "verify.py" in technical and "frame_check.py" in technical and "раскадровка" in technical


def test_no_script_compares_screen_numbers_or_names_with_the_speech() -> None:
    for name, text in _all_docs().items():
        assert "frame_check.py text build/index.html --words " not in text, name
    acceptance = _read("montage-acceptance.md")
    assert re.search(r"^\| 14\.9 \| числа \| приёмщик", acceptance, flags=re.M)
    assert re.search(r"^\| 14\.10 \| содержимое из речи \| приёмщик", acceptance, flags=re.M)


def test_one_rule_on_content_sources() -> None:
    reglament = _read("montage-reglament.md")
    assert "Единое правило источников" in reglament
    for phrase in ("или нейтральные (`ls`, `cd`)", "настоящие команды, пути и метки того инструмента",
                   "настоящую метку инструмента", "из речи или из настоящего инструмента"):
        assert phrase not in reglament, phrase


def test_band_threshold_and_light_share_are_stated_the_same_way() -> None:
    reglament, acceptance = _read("montage-reglament.md"), _read("montage-acceptance.md")
    for text in (reglament, acceptance):
        assert "≤ 250 px" not in text and "> 250 px" not in text and "выше 250 px" not in text
    assert "по времени визуала" not in reglament and "время визуала" not in acceptance
    assert "кадров ролика" not in acceptance
    assert "длительность всего ролика" in reglament.split("**14.6.", 1)[1].split("**14.7.", 1)[0]


def test_single_borrowing_policy_rule_refs_and_publication_wording() -> None:
    reglament = _read("montage-reglament.md")
    assert "«уикенд»" not in reglament and "одна политика" in reglament
    assert "**14.3. Строка субтитра – не больше 3 слов** (правило 7.1)" in reglament
    skill = SKILL.read_text(encoding="utf-8")
    assert "нет ни одного действия, которое публикует" not in skill
    assert "нет API публикации" in skill and "интерфейс приложения" in skill


def test_hook_skeleton_shows_the_stated_number_on_frame_zero() -> None:
    patterns = _read("layout-patterns.md")
    script = patterns.split("### 2.3.", 1)[1].split("### 2.4.", 1)[0]
    assert script.count('class="row" id="sc1-row') == 3
    assert not re.search(r'tl\.set\("#sc1-row\d", \{ opacity: 0', script), "a hook row is hidden on frame 0"
    assert "(0.85-1.0)" not in patterns  # scale range comment matches rule 1.3 (0.55-1.0)
