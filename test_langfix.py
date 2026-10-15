#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""בדיקות למיפוי הפריסות. הרצה: python3 test_langfix.py"""

import unittest

from langfix import (EN_TO_HE, HE_TO_EN, EN_TO_HE_DIR, HE_TO_EN_DIR,
                     convert, detect_direction, fix_text, is_convertible,
                     script_of, wrong_run)


class TestMapping(unittest.TestCase):

    def test_table_is_bijective(self):
        lower = {k: v for k, v in EN_TO_HE.items()
                 if not (k.isalpha() and k.isupper())}
        self.assertEqual(len(set(lower.values())), len(lower),
                         "שני מקשים אנגליים מפיקים אותו תו עברי")
        self.assertEqual(len(HE_TO_EN), len(lower))

    def test_round_trip_every_key(self):
        for en, he in EN_TO_HE.items():
            if en.isalpha() and en.isupper():
                continue
            self.assertEqual(convert(en, EN_TO_HE_DIR), he)
            self.assertEqual(convert(he, HE_TO_EN_DIR), en)

    def test_uppercase_maps_to_same_hebrew(self):
        self.assertEqual(convert("Shalom", EN_TO_HE_DIR),
                         convert("shalom", EN_TO_HE_DIR))

    def test_known_words(self):
        self.assertEqual(convert("akuo", EN_TO_HE_DIR), "שלום")
        self.assertEqual(convert("שלום", HE_TO_EN_DIR), "akuo")
        self.assertEqual(convert("hello", EN_TO_HE_DIR), "יקךךם")
        self.assertEqual(convert("יקךךם", HE_TO_EN_DIR), "hello")

    def test_unmapped_chars_pass_through(self):
        self.assertEqual(convert("abc 123 !@#", EN_TO_HE_DIR),
                         "שנב 123 !@#")

    def test_idempotent_on_already_converted_text(self):
        # אנגלית→עברית לא נוגעת בתווים עבריים, ולהפך — זה מה שמאפשר
        # להריץ שוב על בלוק מורחב בלי להרוס את מה שכבר תוקן.
        self.assertEqual(convert("שלום", EN_TO_HE_DIR), "שלום")
        self.assertEqual(convert("hello", HE_TO_EN_DIR), "hello")

    def test_mixed_block_extension(self):
        # "akuo ers" → הראשונה כבר תוקנה, השנייה עוד לא; הרצה על הבלוק
        # כולו בכיוון en2he משאירה את התיקון ומתקנת את השאר.
        self.assertEqual(convert("שלום ers", EN_TO_HE_DIR), "שלום קרד")


class TestDetection(unittest.TestCase):

    def test_direction(self):
        self.assertEqual(detect_direction("akuo"), EN_TO_HE_DIR)
        self.assertEqual(detect_direction("שלום"), HE_TO_EN_DIR)
        self.assertEqual(detect_direction("שלום ers"), HE_TO_EN_DIR)  # רוב עברי
        self.assertEqual(detect_direction("hello שלו"), EN_TO_HE_DIR)  # רוב לטיני
        self.assertEqual(detect_direction("123 !!"), EN_TO_HE_DIR)    # ברירת מחדל

    def test_convertible_guard(self):
        self.assertFalse(is_convertible("   ", EN_TO_HE_DIR))
        self.assertFalse(is_convertible("12345", EN_TO_HE_DIR))
        self.assertFalse(is_convertible("hello", HE_TO_EN_DIR))
        self.assertTrue(is_convertible("hello", EN_TO_HE_DIR))


class TestWrongRun(unittest.TestCase):
    """הריצה השגויה: מאיפה בשורה מתחילים לתקן."""

    def test_script_of(self):
        self.assertEqual(script_of("akuo"), "en")
        self.assertEqual(script_of("שלום"), "he")
        self.assertIsNone(script_of("123"))
        self.assertIsNone(script_of("!?—"))

    def test_whole_line_when_all_one_language(self):
        self.assertEqual(fix_text("akuo yhkn")[0], "שלום טילמ")
        self.assertEqual(fix_text("שלום טילמ")[0], "akuo yhkn")

    def test_stops_at_correctly_typed_text(self):
        # רק המילה האנגלית מתוקנת; העברית שלפניה הוקלדה נכון ולא נגעת בה.
        self.assertEqual(fix_text("שלום akuo")[0], "שלום שלום")
        self.assertEqual(fix_text("hello שלום")[0], "hello akuo")

    def test_numbers_and_punctuation_do_not_stop_the_run(self):
        self.assertEqual(fix_text("akuo 123 yhkn")[0], "שלום 123 טילמ")
        self.assertEqual(fix_text("akuo !")[0], "שלום !")

    def test_nothing_to_fix(self):
        self.assertEqual(fix_text("123 456"), (None, None))
        self.assertIsNone(fix_text("   ")[0])

    def test_direction_reported(self):
        self.assertEqual(fix_text("akuo")[1], EN_TO_HE_DIR)
        self.assertEqual(fix_text("שלום")[1], HE_TO_EN_DIR)

    def test_forced_direction_is_a_no_op_on_fixed_text(self):
        # זה מה שמונע מלחיצה חוזרת להפוך בחזרה את מה שהרגע תוקן.
        self.assertIsNone(fix_text("שלום", EN_TO_HE_DIR)[0])
        self.assertIsNone(fix_text("akuo", HE_TO_EN_DIR)[0])

    def test_forced_direction_still_finds_leftovers(self):
        self.assertEqual(fix_text("שלום akuo", EN_TO_HE_DIR)[0], "שלום שלום")

    def test_only_the_last_line_is_touched(self):
        # אם הסימון תפס יותר משורה, כל מה שמעל השורה האחרונה חוזר כמו שהוא.
        self.assertEqual(fix_text("שלום עולם\nakuo")[0], "שלום עולם\nשלום")
        self.assertEqual(fix_text("keep this\nakuo")[0], "keep this\nשלום")

    def test_hebrew_line_round_trip(self):
        # מוצמד כאן אחרי שציפייה שגויה בבדיקת e2e נראתה כמו באג.
        self.assertEqual(fix_text("שלום עולם")[0], "akuo guko")
        self.assertEqual(fix_text("akuo guko")[0], "שלום עולם")

    def test_run_start_index(self):
        start, direction = wrong_run("שלום akuo")
        self.assertEqual(start, len("שלום "))
        self.assertEqual(direction, EN_TO_HE_DIR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
