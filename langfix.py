#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
langfix — תיקון טקסט שהוקלד בשפת מקלדת שגויה (עברית ⇄ אנגלית) ב־macOS.

הפעלה רגילה (מתוך Quick Action, ללא ארגומנטים):
    יש טקסט מסומן → הוא מומר.
    אין סימון      → נבחר הטקסט עד תחילת השורה/הפסקה, ומומר ממנו כל מה
                     שהוקלד בשפה הלא נכונה — עד המילה הראשונה שכבר בשפה
                     השנייה.

מצבי בדיקה:
    echo "akuo" | python3 langfix.py --stdin
    python3 langfix.py --text "akuo"
"""

import os
import re
import subprocess
import sys
import time

# ── הגדרות שניתן לשנות ─────────────────────────────────────────────────
SWITCH_INPUT_SOURCE = True      # לשלוח Ctrl+Space אחרי ההמרה
REPEAT_GUARD_SEC = 2.5          # לחיצה חוזרת בתוך X שניות לא הופכת חזרה
STATE_PATH = "/tmp/langfix.state"
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "langfix.log")
DEBUG_LOG = True                # לוג אבחון; אפשר לכבות אחרי שהכול עובד
CLIPBOARD_POLL_SEC = 0.4        # כמה זמן לחכות שה־⌘C יעדכן את הלוח
CLIPBOARD_POLL_STEP = 0.02
# ───────────────────────────────────────────────────────────────────────

# פריסת "עברית" הסטנדרטית של macOS: מקש אנגלי → התו שמופק בעברית.
EN_TO_HE = {
    "q": "/", "w": "'", "e": "ק", "r": "ר", "t": "א",
    "y": "ט", "u": "ו", "i": "ן", "o": "ם", "p": "פ",
    "a": "ש", "s": "ד", "d": "ג", "f": "כ", "g": "ע",
    "h": "י", "j": "ח", "k": "ל", "l": "ך", ";": "ף", "'": ",",
    "z": "ז", "x": "ס", "c": "ב", "v": "ה", "b": "נ",
    "n": "מ", "m": "צ", ",": "ת", ".": "ץ", "/": ".",
}
# אין רישיות בעברית — אות גדולה מפיקה את אותו תו עברי.
EN_TO_HE.update({k.upper(): v for k, v in EN_TO_HE.items() if k.isalpha()})

# הטבלה ההפוכה נגזרת מהמיפוי בין האותיות הקטנות בלבד (המיפוי חד־חד־ערכי).
HE_TO_EN = {v: k for k, v in EN_TO_HE.items() if not (k.isalpha() and k.isupper())}

HEB_LETTERS = range(0x05D0, 0x05EB)  # א..ת, כולל סופיות

HE_TO_EN_DIR = "he2en"
EN_TO_HE_DIR = "en2he"

SRC_OF_DIR = {HE_TO_EN_DIR: "he", EN_TO_HE_DIR: "en"}
DIR_OF_SRC = {"he": HE_TO_EN_DIR, "en": EN_TO_HE_DIR}


# ── המרה טהורה (זה החלק שהטסטים בודקים) ────────────────────────────────

def convert(text, direction):
    """ממיר טקסט לפי כיוון נתון. תווים שאינם בטבלה עוברים כמו שהם."""
    table = HE_TO_EN if direction == HE_TO_EN_DIR else EN_TO_HE
    return "".join(table.get(ch, ch) for ch in text)


def detect_direction(text):
    """עברית→אנגלית אם יש בטקסט יותר אותיות עבריות מלטיניות, אחרת להפך."""
    heb = sum(1 for ch in text if ord(ch) in HEB_LETTERS)
    lat = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    return HE_TO_EN_DIR if heb > lat else EN_TO_HE_DIR


def is_convertible(text, direction):
    """האם יש בכלל מה להמיר? מונע 'תיקון' של רווחים או מספרים."""
    table = HE_TO_EN if direction == HE_TO_EN_DIR else EN_TO_HE
    return any(ch in table for ch in text)


_WORDS = re.compile(r"\S+")


def script_of(word):
    """'he' / 'en' / None (מילה בלי אותיות — מספר, פיסוק)."""
    for ch in word:
        if ord(ch) in HEB_LETTERS:
            return "he"
    for ch in word:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            return "en"
    return None


def wrong_run(text, force_src=None):
    """
    מאיזה מקום בטקסט להתחיל להמיר, ובאיזה כיוון.

    'הריצה השגויה' היא המילה האחרונה שיש בה אותיות, וכל מה שלפניה באותה
    שפה — עד המילה הראשונה שכבר בשפה השנייה, שהיא הסימן שמשם הטקסט הוקלד
    נכון. שורה שכולה באותה שפה מומרת במלואה, וזה המצב הרגיל.

    מילים בלי אותיות (מספרים, פיסוק) לא עוצרות את הריצה ולא פותחות אותה.
    מחזיר (start, direction), או (None, None) אם אין מה להמיר.
    """
    words = [(m.start(), script_of(m.group())) for m in _WORDS.finditer(text)]
    src = force_src or next((s for _, s in reversed(words) if s), None)
    if src is None:
        return None, None
    anchors = [pos for pos, s in words if s == src]
    if not anchors:
        return None, None

    start = anchors[-1]
    for pos, s in reversed(words):
        if pos > start:
            continue          # מה שאחרי המילה האחרונה נגרר איתה ממילא
        if s is not None and s != src:
            break             # כאן מתחיל טקסט שהוקלד נכון — עוצרים
        start = pos
    return start, DIR_OF_SRC[src]


def fix_text(text, force_direction=None):
    """
    מקבל בלוק טקסט ומחזיר אותו אחרי תיקון הריצה השגויה שבסופו,
    יחד עם הכיוון שנבחר. (None, None) אם אין מה לתקן.

    התיקון מוגבל לשורה האחרונה: "מה שהרגע הקלדתי" לא חוצה שורה, וגם אם
    הסימון תפס יותר — כל מה שלפני השורה האחרונה חוזר כמו שהוא.
    """
    head, sep, tail = text.rpartition("\n")
    force_src = SRC_OF_DIR.get(force_direction)
    start, direction = wrong_run(tail, force_src)
    if start is None:
        return None, None
    fixed = head + sep + tail[:start] + convert(tail[start:], direction)
    return (fixed, direction) if fixed != text else (None, direction)


# ── שכבת מערכת ─────────────────────────────────────────────────────────

_blocked = []   # מתמלא אם macOS חוסם שליחת מקשים (חסרה הרשאת נגישות)


def log(msg):
    """
    שורה לכל הפעלה. בלי זה, קיצור שלא עובד נראה בדיוק כמו קיצור שלא נתפס
    בכלל — והלוג הוא מה שמבדיל בין השניים.
    """
    if not DEBUG_LOG:
        return
    try:
        if os.path.getsize(LOG_PATH) > 200_000:
            os.remove(LOG_PATH)
    except OSError:
        pass
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except (IOError, OSError):
        pass


def osa(script):
    """מריץ AppleScript. מחזיר True אם הצליח."""
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       check=True, capture_output=True, timeout=10)
        return True
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")
        if "1002" in err or "not allowed" in err or "assistive" in err.lower():
            _blocked.append(err)
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


def warn_no_accessibility():
    """
    בלי הרשאת נגישות שום מקש לא נשלח והקיצור פשוט 'לא עושה כלום'.
    מוטב להגיד את זה במפורש — פעם בשעה, כדי לא להציק.
    """
    marker = "/tmp/langfix.nag"
    try:
        if time.time() - os.path.getmtime(marker) < 3600:
            return
    except OSError:
        pass
    try:
        open(marker, "w").close()
    except (IOError, OSError):
        pass
    osa('display alert "תיקון שפה — חסרה הרשאה" message '
        '"macOS חוסם שליחת מקשים.\n\n'
        'לוחצים על הקיצור פעם אחת בשדה טקסט ומאשרים את הבקשה שקופצת." '
        'as critical')


def key(code, modifiers=(), repeat=1):
    """שולח key code דרך System Events (כולל דרישת הרשאת Accessibility)."""
    mods = ""
    if modifiers:
        mods = " using {%s}" % ", ".join("%s down" % m for m in modifiers)
    lines = "\n".join(["key code %d%s" % (code, mods)] * repeat)
    return osa('tell application "System Events"\n%s\nend tell' % lines)


# key codes
K_C, K_V, K_SPACE = 8, 9, 49
K_LEFT, K_RIGHT, K_DOWN, K_UP = 123, 124, 125, 126

# איך לסמן את מה שהוקלד, כשהמשתמש לא סימן כלום. הראשון שמחזיר טקסט מנצח.
#
# ⌥⇧↑ ראשון בכוונה: הוא בוחר עד תחילת הפסקה והוא **חסין לכיוון** — החיצים
# האופקיים ב־macOS זזים ויזואלית, כך שבשורה עברית "אחורה" הוא דווקא ימינה,
# ולכן כל אחד מהם מופיע כאן בשני הכיוונים.
SELECT_STRATEGIES = (
    ("paragraph",  K_UP,    ("option", "shift")),
    ("line-left",  K_LEFT,  ("command", "shift")),
    ("line-right", K_RIGHT, ("command", "shift")),
    ("word-left",  K_LEFT,  ("option", "shift")),
    ("word-right", K_RIGHT, ("option", "shift")),
)
MAX_SELECTION_LINES = 3   # סימון גדול מזה נחשב פספוס של האסטרטגיה


def clip_read():
    try:
        out = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, timeout=5)
        return out.stdout.decode("utf-8", "replace")
    except (subprocess.SubprocessError, OSError):
        return ""


def clip_write(text):
    try:
        p = subprocess.Popen(["/usr/bin/pbcopy"], stdin=subprocess.PIPE)
        p.communicate(text.encode("utf-8"), timeout=5)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


SENTINEL = "\x00langfix\x00"


def copy_selection():
    """
    מעתיק את הסימון הנוכחי ומחזיר את הטקסט, או None אם אין סימון.
    עובד בעזרת ערך סימון בלוח: אם הלוח לא השתנה — לא הועתק כלום.
    """
    clip_write(SENTINEL)
    if not key(K_C, ("command",)):
        return None
    deadline = time.time() + CLIPBOARD_POLL_SEC
    while time.time() < deadline:
        cur = clip_read()
        if cur != SENTINEL:
            return cur
        time.sleep(CLIPBOARD_POLL_STEP)
    return None


def collapse_selection(text):
    """
    מבטל סימון שלא נשתמש בו. לחיצת חץ בודדת מכנסת את הסימון בלי למחוק
    כלום; הכיוון נבחר לפי שפת הטקסט, כי הסמן המקורי היה בקצה הלוגי שלו.
    """
    key(K_LEFT if detect_direction(text) == HE_TO_EN_DIR else K_RIGHT)


def select_typed_text():
    """
    מסמן את מה שהוקלד עד הסמן ומחזיר (טקסט, שם האסטרטגיה).
    (None, None) אם שום אסטרטגיה לא בחרה טקסט.
    """
    for name, code, mods in SELECT_STRATEGIES:
        if not key(code, mods):
            return None, None          # חסום — אין טעם להמשיך לנסות
        text = copy_selection()
        if text and text.strip():
            if text.count("\n") >= MAX_SELECTION_LINES:
                # אסטרטגיה שבלעה חצי מסמך: ההדבקה תחזיר את הכול מילה במילה,
                # אבל היא כן תשטח עיצוב בטווח הזה. עדיף לנסות משהו צר יותר.
                collapse_selection(text)
                continue
            return text, name
        # לא נבחר כלום, ולכן אין סימון לכנס — ממשיכים לאסטרטגיה הבאה.
    return None, None


# ── שמירת הכיוון בין לחיצות ─────────────────────────────────────────────

def read_state():
    """הכיוון של הלחיצה הקודמת, אם היא הייתה ממש עכשיו."""
    try:
        with open(STATE_PATH, "r") as fh:
            ts, direction = fh.read().split("\t")
        if time.time() - float(ts) <= REPEAT_GUARD_SEC:
            return direction
    except (IOError, OSError, ValueError):
        pass
    return None


def write_state(direction):
    try:
        with open(STATE_PATH, "w") as fh:
            fh.write("%f\t%s" % (time.time(), direction))
    except (IOError, OSError):
        pass


def clear_state():
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


# ── הזרימה המלאה ───────────────────────────────────────────────────────

def run():
    log("run: start")
    saved_clip = clip_read()

    def restore():
        if saved_clip != SENTINEL:
            time.sleep(0.15)   # לתת ל־⌘V לקרוא מהלוח לפני ההחזרה
            clip_write(saved_clip)

    # לחיצה חוזרת מיד אחרי תיקון נועלת את הכיוון, כדי שהיא לא תהפוך בחזרה
    # את מה שהרגע תוקן.
    forced = read_state()

    text = copy_selection()
    manual = bool(text and text.strip())
    how = "selection"
    if not manual:
        text, how = select_typed_text()

    if not text or not text.strip():
        log("run: nothing to convert (blocked=%s)" % bool(_blocked))
        clear_state()
        restore()
        return

    if manual:
        # סימון ידני = כוונה מפורשת: ממירים את כל מה שסומן.
        direction = forced or detect_direction(text)
        fixed = convert(text, direction) if is_convertible(text, direction) else None
        if fixed == text:
            fixed = None
    else:
        fixed, direction = fix_text(text, forced)

    log("run: %s %r dir=%s" % (how, text, direction))

    if not fixed:
        if not manual:
            collapse_selection(text)
        log("run: nothing to fix in it")
        clear_state()
        restore()
        return

    clip_write(fixed)
    if not key(K_V, ("command",)):
        log("run: paste failed")
        restore()
        return

    write_state(direction)
    if SWITCH_INPUT_SOURCE:
        key(K_SPACE, ("control",))

    log("run: pasted %r" % fixed)
    restore()


def cli_file(path, mode, force=None):
    """
    הממשק ש‑Hammerspoon קורא לו: קלט מקובץ (כדי שלא יהיה ציטוט shell על
    טקסט שרירותי), פלט = שורת הכיוון ואחריה הטקסט המתוקן כמו שהוא.
    'none' פירושו שאין מה לתקן.

    mode="all" — להמיר את כל מה שנתקבל (המשתמש סימן במפורש).
    mode="fix" — רק הריצה השגויה שבסוף (לא היה סימון).
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (IOError, OSError):
        sys.stdout.write("none\n")
        return 0

    if mode == "all":
        direction = force or detect_direction(text)
        fixed = convert(text, direction) if is_convertible(text, direction) else None
        if fixed == text:
            fixed = None
    else:
        fixed, direction = fix_text(text, force)

    if not fixed:
        sys.stdout.write("none\n")
        return 0
    sys.stdout.write(direction + "\n")
    sys.stdout.write(fixed)
    return 0


def main(argv):
    if "--file" in argv:
        path = argv[argv.index("--file") + 1]
        mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "fix"
        return cli_file(path, mode)
    if "--stdin" in argv:
        text = sys.stdin.read().rstrip("\n")
        sys.stdout.write(convert(text, detect_direction(text)) + "\n")
        return 0
    if "--text" in argv:
        text = argv[argv.index("--text") + 1]
        sys.stdout.write(convert(text, detect_direction(text)) + "\n")
        return 0
    if "--fix" in argv:
        text = argv[argv.index("--fix") + 1]
        fixed, _ = fix_text(text)
        sys.stdout.write((fixed if fixed else text) + "\n")
        return 0
    run()
    if _blocked:
        log("BLOCKED: %s" % _blocked[0].strip().replace("\n", " ")[:200])
        warn_no_accessibility()
    # Automator פותח דיאלוג שגיאה על כל exit code שאינו 0, וכל אחד ממסלולי
    # ה"אין מה לתקן" הוא מצב תקין לגמרי (הסמן לא בשדה טקסט, נבחרו רווחים).
    # כשלים אמיתיים מדווחים בלוג ובהתראה — לא דרך קוד היציאה.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
