1 // 1; r"""
window.AI_GRADER_SRC = (function () {/*
"""
# ==PY-START==
# السطور اللي فوق دي بتخلي المتصفح يقدر يحمّل الملف ده ويشغّله بـ Pyodide (Python جوه المتصفح)،
# ومن غير ما تأثر على Python العادي. (متكتبش علامة نجمة وبعدها شرطة مايلة في أي حتة في الملف)
# -*- coding: utf-8 -*-
"""
=====================================================================
  المصحح الذكي — ai_grader.py
  ذكاء اصطناعي بيصحح امتحانات المنصة (ملف واحد كامل)
=====================================================================

  طريقة التشغيل:
      python ai_grader.py            (بيشغّل المنصة ويفتحها في المتصفح)
      python ai_grader.py --test     (بيجرب التصحيح على إجابات نموذجية وغلط)
      python ai_grader.py --retrain  (بيعيد تدريب الشبكة من الأول)

  مش محتاج أي مكتبات خارجية — Python 3.8 أو أحدث بس.

  الذكاء ده متعلّم بالكامل من المنهج اللي جوه المنصة (ملف script.js):
    ١) القارئ       : بيقرأ الدروس والأسئلة من script.js
    ٢) قاعدة المعرفة : بيقسّم الدروس لجمل ويعرف أهمية كل كلمة (idf)
    ٣) معالجة اللغة  : بيشيل التشكيل ويوحّد الهمزات والتاء المربوطة والأرقام
    ٤) تمثيل الكلمات : شبكة Skip-gram بتتعلم معنى الكلمات من سياقها في الدروس
    ٥) الإدراك       : بيستخرج ١٣ خاصية من إجابة الطالب
    ٦) الشبكة العصبية: خلايا عصبية (١٣ ← ١٠ ← ١) بتقدّر جودة الإجابة
    ٧) التدريب       : بيولّد آلاف الإجابات (صح وناقصة وغلط) من المنهج ويتعلم منها
    ٨) التفكير       : بيراجع كل فكرة مطلوبة، ويكتب خطوات تفكيره، ويدي الدرجة
    ٩) المراجعة      : طابور بيصحح الامتحانات واحد واحد وبيحفظ النتائج
   ١٠) الخادم        : بيشغّل المنصة على http://localhost:8765 وبيستقبل الإجابات
=====================================================================
"""

import hashlib
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import uuid

BROWSER = sys.platform == "emscripten"  # شغال جوه المتصفح (Pyodide)
if not BROWSER:
    import webbrowser
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
else:
    SimpleHTTPRequestHandler, ThreadingHTTPServer = object, None

try:  # عشان العربي يظهر صح في شاشة الأوامر على ويندوز
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------
# الإعدادات
# ---------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(globals().get("__file__", "ai_grader.py")))  # جوه المتصفح مفيش ملف
SCRIPT_JS = os.path.join(HERE, "script.js")
MODEL_FILE = os.path.join(HERE, "ai_model.json")
RESULTS_FILE = os.path.join(HERE, "ai_results.json")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8765))  # على الاستضافة (زي Render) البورت بيتحدد من الموقع نفسه
ONLINE = "PORT" in os.environ             # شغال على النت مش على جهاز
VERSION = "1.0"
SEED = 2026

REVIEW_SECONDS_OBJECTIVE = 1.2   # وقت مراجعة السؤال الموضوعي
REVIEW_SECONDS_ESSAY = 3.0       # وقت مراجعة السؤال المقالي (المصحح بيفكر أكتر)
PASS_PERCENT = 50                # نسبة النجاح
MARKS = {"mcq": 1, "tf": 1, "fill": 1, "match": 2}

AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"


def to_ar(x):
    """تحويل الأرقام لأرقام عربية عشان تظهر للطالب"""
    s = str(x)
    if s.endswith(".0"):
        s = s[:-2]
    return "".join(AR_DIGITS[int(c)] if c.isdigit() and c.isascii() else ("٫" if c == "." else c) for c in s)


def say(msg):
    print(msg, flush=True)


# =====================================================================
# ١) القارئ: بيقرأ بيانات المنهج من script.js
#    (البيانات مكتوبة بصيغة JavaScript فعملنا محلل صغير ليها)
# =====================================================================
class JSLiteralParser:
    ID_RE = re.compile(r"[A-Za-z_$][\w$]*")
    NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

    def __init__(self, text, pos=0):
        self.s, self.i, self.n = text, pos, len(text)

    def error(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError("خطأ في قراءة script.js (سطر %d): %s" % (line, msg))

    def ws(self):
        s = self.s
        while self.i < self.n:
            c = s[self.i]
            if c in " \t\r\n\ufeff":
                self.i += 1
            elif s.startswith("//", self.i):
                j = s.find("\n", self.i)
                self.i = self.n if j < 0 else j + 1
            elif s.startswith("/" + "*", self.i):
                j = s.find("*" + "/", self.i + 2)
                self.i = self.n if j < 0 else j + 2
            else:
                break

    def peek(self):
        self.ws()
        if self.i >= self.n:
            self.error("الملف خلص فجأة")
        return self.s[self.i]

    def value(self):
        c = self.peek()
        if c == "[":
            return self.array()
        if c == "{":
            return self.obj()
        if c in "\"'":
            return self.string()
        if c == "-" or c.isdigit():
            m = self.NUM_RE.match(self.s, self.i)
            self.i = m.end()
            t = m.group(0)
            return float(t) if "." in t else int(t)
        word = self.ident()
        consts = {"true": True, "false": False, "null": None, "undefined": None}
        if word in consts:
            return consts[word]
        self.error("قيمة مش مفهومة: " + word)

    def ident(self):
        m = self.ID_RE.match(self.s, self.i)
        if not m:
            self.error("متوقع اسم")
        self.i = m.end()
        return m.group(0)

    def array(self):
        self.i += 1
        out = []
        while True:
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "]":
                self.error("متوقع , أو ]")

    def obj(self):
        self.i += 1
        out = {}
        while True:
            c = self.peek()
            if c == "}":
                self.i += 1
                return out
            key = self.string() if c in "\"'" else self.ident()
            if self.peek() != ":":
                self.error("متوقع :")
            self.i += 1
            out[key] = self.value()
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "}":
                self.error("متوقع , أو }")

    def string(self):
        q = self.s[self.i]
        self.i += 1
        buf = []
        esc = {"n": "\n", "t": "\t", "r": "", "b": "", "f": "", "0": ""}
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                nx = self.s[self.i + 1]
                if nx == "u":
                    buf.append(chr(int(self.s[self.i + 2:self.i + 6], 16)))
                    self.i += 6
                    continue
                buf.append(esc.get(nx, nx))
                self.i += 2
            elif c == q:
                self.i += 1
                return "".join(buf)
            else:
                buf.append(c)
                self.i += 1
        self.error("نص مش مقفول")


def read_platform(path=SCRIPT_JS):
    """بيرجّع (الأقسام، الأسئلة، اسم المنصة، بصمة الملف)"""
    with open(path, encoding="utf-8") as f:
        src = f.read()

    def grab(name):
        k = src.find("const %s = " % name)
        if k < 0:
            raise ValueError("مش لاقي %s في script.js" % name)
        return JSLiteralParser(src, src.index("[", k)).value()

    m = re.search(r'const SITE\s*=\s*\{[^}]*?name:\s*"([^"]+)"', src, re.S)
    subjects, questions = grab("SUBJECTS"), grab("QUESTIONS")
    return subjects, questions, (m.group(1) if m else "المنصة"), data_digest(subjects, questions)


def data_digest(subjects, questions):
    raw = json.dumps([subjects, questions], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


PY_MARKS = ("# ==PY-" + "START==", "# ==PY-" + "END==")


def code_digest():
    """بصمة كود المصحح نفسه (لو الكود اتغير، الشبكة بتتدرب تاني)"""
    try:
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            src = f.read()
    except Exception:
        src = globals().get("__browser_src__", "")
    a, b = src.find(PY_MARKS[0]), src.rfind(PY_MARKS[1])
    if a >= 0 and b > a:
        src = src[a:b]
    return hashlib.sha1(src.replace("\r\n", "\n").encode("utf-8")).hexdigest()[:10]


# =====================================================================
# ٣) معالجة اللغة العربية
# =====================================================================
DIAC_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
PUNCT_RE = re.compile(r"[^\w\s]|_")
DIGIT_MAP = {ord(a): str(i) for i, a in enumerate(AR_DIGITS)}
DIGIT_MAP.update({ord(a): str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")})


def normalize(text):
    """بيشيل التشكيل والتطويل ويوحّد (أ إ آ ← ا) و(ة ← ه) و(ى ← ي) والأرقام"""
    t = DIAC_RE.sub("", str(text or ""))
    t = re.sub("[أإآٱ]", "ا", t)
    t = t.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    t = t.translate(DIGIT_MAP)
    t = PUNCT_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _n(words):
    return set(normalize(w) for w in words.split())


STOPWORDS = _n(
    "في من على الى إلى عن مع هو هي هم هن انت أنا نحن هذا هذه ذلك تلك هؤلاء الذي التي الذين اللذان "
    "ثم او أو ام أم بل قد لقد كان كانت يكون تكون كانوا و ف ب ل ك ما ماذا لماذا كيف متى اين هل "
    "كل بعض اي أي عند عندما حيث حين إذا اذا لكن لكنه ولكن أن إن ان انه أنه إنه انها أنها لأن لان "
    "بين حتى منذ خلال بعد قبل فوق تحت إلا الا غير سوى كما مثل ايضا أيضا جدا فقط لها له لهم به بها "
    "فيه فيها منه منها عليه عليها اليه إليها وهو وهي وقد وكان وكانت ذات يعني ده دي دا اللي علشان عشان "
    "يا اه آه كده كدا بتاع بتاعت هو هيا احنا انتم ليه ازاي امتى فين ايه إيه وهكذا تم يتم"
)
NEGATIONS = _n("لا لم لن ليس ليست ليسوا غير بدون مش مفيش ماكانش عدم")
SOFT_NEG = _n("صعب يصعب يستحيل مستحيل")          # «يصعب تفتيته» = مش بيتفكك
NEG_EXCEPT = _n("الا إلا سوى")                    # «لا ... إلا» معناها إثبات مش نفي
NOT_COLLOQ_NEG = _n("معاش مشمش منكمش مغشوش منقوش مدهوش")


def is_neg(w):
    """أداة نفي، حتى لو لازقة فيها واو أو فاء: «ولم» «ولا» «فمش»"""
    return w in NEGATIONS or (len(w) >= 3 and w[0] in "وف" and w[1:] in NEGATIONS)
DONT_KNOW = [normalize(x) for x in ["لا أعرف", "لا اعرف", "مش عارف", "معرفش", "مش فاكر", "لا أدري", "مش عارفة", "مش متأكد"]]

PREFIXES = ("وبال", "وال", "بال", "كال", "فال", "ولل", "لل", "ال")
ONE_PREFIX = ("و", "ف", "ب", "ل", "ك")
SUFFIXES = ("هما", "كما", "تان", "تين", "ات", "ون", "ين", "ان", "يه", "ها", "هم", "هن", "كم", "نا", "وا", "ه", "ي", "ا")

_stem_cache = {}
WAW_ROOTS = set()  # كلمات الواو فيها أصلية (الوحدة، الوطن، الوعي) — بتتملى من المنهج نفسه


def waw_root(s):
    """«وحدته» ← وحد ، «وعيهم» ← وعي — لو الكلمة من الكلمات اللي الواو فيها أصلية في المنهج"""
    r = s
    for suf in SUFFIXES:
        if r.endswith(suf) and len(r) - len(suf) >= 3:
            r = r[: -len(suf)]
            break
    for c in (r, r[:-1] if r.endswith("ت") else None):
        if c and c in WAW_ROOTS:
            return c
    return None


def stem(w):
    """مجذّع خفيف: بيشيل (ال) وحروف العطف والجر والضمائر وعلامات الجمع"""
    if w in _stem_cache:
        return _stem_cache[w]
    s = w
    if not s.isdigit():
        for p in PREFIXES:
            if s.startswith(p) and len(s) - len(p) >= 2:
                s = s[len(p):]
                break
        else:
            wr = waw_root(s) if s[:1] == "و" and WAW_ROOTS else None
            if wr:
                _stem_cache[w] = wr
                return wr
            if s[:1] in ONE_PREFIX and len(s) >= 5:
                s = s[1:]
                for p in PREFIXES:  # زي «وبالتالي» أو «فالدولة»
                    if s.startswith(p) and len(s) - len(p) >= 2:
                        s = s[len(p):]
                        break
        for _ in range(2):
            for suf in SUFFIXES:
                if s.endswith(suf) and len(s) - len(suf) >= 3:
                    s = s[: -len(suf)]
                    break
            else:
                break
    _stem_cache[w] = s
    return s


SKEL_PREFIXES = ("بي", "بت", "بن", "هي", "هت", "هن", "مست", "است", "يست", "تست", "نست", "ست", "ي", "ت", "ن", "م", "ا")
_skel_cache = {}


def skeleton(s):
    """هيكل الكلمة (قريب من الجذر): «يزرعوا» و«مزارع» ← زرع ، «بيجمعوا» و«جامع» ← جمع
    بيشيل حروف المضارعة والعامية (بي، هي) وأوزان (م، است) وحروف المد"""
    k = _skel_cache.get(s)
    if k is None:
        w = s
        if not w.isdigit():
            for p in SKEL_PREFIXES:
                if w.startswith(p) and len(w) - len(p) >= 3:
                    w = w[len(p):]
                    break
            w = w[0] + re.sub("[اوي]", "", w[1:]) if w else w
        k = _skel_cache[s] = w
    return k


def words(text):
    return normalize(text).split()


def content_tokens(text):
    """الكلمات المهمة بس (من غير الكلمات الشائعة وأدوات النفي) بعد التجذيع"""
    return [stem(w) for w in words(text) if w not in STOPWORDS and not is_neg(w) and (len(w) > 1 or w.isdigit())]


def colloq_neg(w):
    """النفي بالعامية: «مرفضوش» ← رفض ، «متأثرش» ← تأثر ، «محدش» ← حد"""
    if len(w) >= 5 and w[0] == "م" and w.endswith("ش") and w not in NOT_COLLOQ_NEG:
        core = w[1:-1]
        core = core[1:] if core.startswith("ا") else core
        core = core[:-1] if core.endswith("و") else core
        return core if len(core) >= 2 else None
    return None


def tagged_tokens(text):
    """الكلمات المهمة ومعاها علامة: هل الكلمة دي منفية ولا لأ؟
    أداة النفي بتأثر على الـ٣ كلمات اللي بعدها لحد آخر الجملة الفرعية"""
    out = []
    for chunk in re.split(r"[.!؟?؛;،,:\n\-–]+", str(text or "")):
        ws = words(chunk)
        exc = any(w in NEG_EXCEPT for w in ws)
        scope, soft = 0, False
        for w in ws:
            if is_neg(w):
                scope, soft = (0 if exc else 3), False
                continue
            if w in SOFT_NEG or (len(w) >= 4 and w[0] in "وف" and w[1:] in SOFT_NEG):  # «يصعب تفتيته»: النفي على الكلمة اللي بعدها بس
                scope, soft = (0 if exc else 2), True  # «صعب حد يفتت»: لحد الفعل، وبتقف عند أي واو
                continue
            core = colloq_neg(w)
            if core:
                if not exc:
                    out.append((stem(core), True))
                    scope = 2
                    continue
                w = core
            if w in STOPWORDS or (len(w) <= 1 and not w.isdigit()):
                continue
            if scope and w.startswith("و") and len(w) >= 4 and (soft or not w.startswith("وال")):
                scope = 0  # «... ولم يلتف» أو «... وقاوموا»: جملة جديدة
            out.append((stem(w), scope > 0))
            scope = max(0, scope - 1)
    return out


def split_sentences(text):
    parts = re.split(r"[.!؟?؛;\n]+|\s[١٢٣٤٥٦٧٨٩]\)|\s\d\)|\(أ\)|\(ب\)|\(ج\)|\(د\)", " " + str(text or ""))
    return [p.strip(" ،,:-–") for p in parts if p and len(p.strip()) > 1]


def split_points(text, min_tokens=2):
    """تقسيم الإجابة النموذجية لأفكار (جمل وجمل فرعية)"""
    out = []
    for s in split_sentences(text):
        for c in re.split(r"[،,:–]|\sو(?=ال)", s):
            c = c.strip()
            if len(content_tokens(c)) >= min_tokens:
                out.append(c)
    return out or [text]


# كلمات عامة (مش معلومة في حد ذاتها) — وزنها أقل عشان الطالب ميتحاسبش لو كتب الفكرة بأسلوبه
LIGHT_WORDS = set(stem(w) for w in _n(
    "وجود يوجد توجد تحول تحولت قديم القديم امتلاك امتلك منح منحت منحتهم نهاية بداية خلال جانب أهم اهم أصبح أصبحت "
    "حول أدى ادي أدت قيام عملية يعد تعد يعتبر تعتبر حدث حدثت عدد كبير كبيرة صغير جديد مختلف عدة عدم أساس نتيجة سبب أسباب "
    "دور أمر شكل وسيلة مهم مهمة ذكر يمكن تمثل يمثل قام قامت إلى ضد مثلت تشكيل جعل جعلت دعم عبر أتاح أتاحت فرصة "
    "أسهم أسهمت ظهر أظهر تمتع تمتعت نتج حيث وهو مما"))

# قاموس لغوي عام صغير (مرادفات وعامية مصرية) — مش معلومات تاريخية، بس بيفهم إن الكلمتين بنفس المعنى
SYNONYM_GROUPS = [
    "سلطة حكومة حكم", "نهاية آخر", "مياه مية ماء", "جيش عسكري قوات", "ضعف ضعيفة واهن", "قوة قوية قوي",
    "أسلحة سلاح", "صراع نزاع خلاف", "حماية حمى يحمي", "طريق طرق", "ربط يربط وصل يوصل", "أرض أراضي",
    "مركز محور", "أجنبي أجانب", "احتلال استعمار محتل", "بريطاني إنجليز إنجليزي الإنجليز", "حماية تحمي بتحمي حمت",
    "استقرار ثبات أمان أمن", "سياسة سياسات", "حاجز حواجز سور أسوار درع حصن", "زراعة زراعي فلاح فلاحين زرع يزرع",
    "سكان ناس أهالي أهل", "ثقافة ثقافي عادات تقاليد", "تجانس متجانس تشابه يشبه شبه", "حدود بحر بحار صحراء صحاري صحرا", "إجبار أجبر أجبرت اضطر اضطروا", "إعادة تغيير يغير يغيروا غيروا", "تعزيز زيادة زاد يزيد تقوية", "ترسيخ تثبيت", "تفتيت تفكك يتفكك تفتيته يفرق تفريق تفرق", "اتحد توحد وحدة التفاف", "جعل خلى", "مثل زي", "لأن عشان علشان",
    "أيضا كمان برضه", "جدا أوي", "الآن دلوقتي", "كثير كتير", "أصبح بقى بقت", "شيء حاجة",
]
SYN_ID = {}
for _gi, _g in enumerate(SYNONYM_GROUPS):
    for _w in _g.split():
        _x = normalize(_w)
        for _v in (_x, "ال" + _x, "و" + _x):  # كل صيغ الكلمة (بريطاني / البريطاني / والبريطاني)
            SYN_ID[stem(_v)] = _gi

# أضداد: لو الفكرة فيها كلمة من ناحية والطالب كتب عكسها (تعزيز ← ضعف) يبقى عكس المعنى
ANTONYM_PAIRS = [
    ("تعزيز زيادة زاد يزيد تقوية قوة قوي قوية ترسيخ ازدهار تقدم", "ضعف ضعيف ضعيفة أضعف يضعف تراجع نقص قلة انهيار تدهور"),
    ("استقرار ثبات أمان أمن آمن هدوء", "اضطراب فوضى فوضي"),
    ("وحدة اتحاد اتحد توحد متماسك تماسك التفاف تجانس متجانس انصهار", "تفكك انقسام انقسم تفرق تفتيت تشتت تمزق"),
    ("رفض رفضوا قاوم مقاومة", "قبول قبلوا رحب رحبوا استسلام خضوع خضع استسلم"),
    ("حفاظ حافظ حماية يحمي", "ضياع فقدان طمس ذوبان تهديد"),
    ("طبيعي طبيعية", "صناعي صناعية مصطنع"),
]
ANT, ANT_SK = {}, {}
for _pi, _pair in enumerate(ANTONYM_PAIRS):
    for _side, _grp in enumerate(_pair):
        for _w in _grp.split():
            _x = normalize(_w)
            for _v in (_x, "ال" + _x, "و" + _x):
                ANT[stem(_v)] = (_pi, _side)
                _k = skeleton(stem(_v))
                if len(_k) >= 3:
                    ANT_SK[_k] = (_pi, _side)


def ant_of(t):
    r = ANT.get(t)
    if r is None:
        k = skeleton(t)
        r = ANT_SK.get(k) if len(k) >= 3 else None
    return r


_gram_cache = {}


def grams(w):
    g = _gram_cache.get(w)
    if g is None:
        x = "<" + w + ">"
        g = frozenset(x[i:i + 3] for i in range(len(x) - 2))
        _gram_cache[w] = g
    return g


def dice(a, b):
    if a == b:
        return 1.0
    ga, gb = grams(a), grams(b)
    return 2.0 * len(ga & gb) / (len(ga) + len(gb)) if ga and gb else 0.0


NUM_RE = re.compile(r"\d+")


def numbers(text):
    return set(NUM_RE.findall(normalize(text)))


# =====================================================================
# ٢) قاعدة المعرفة: تنظيم معلومات المنهج
# =====================================================================
class KnowledgeBase:
    def __init__(self, subjects, questions):
        self.lessons = {}
        self.questions = {q["id"]: q for q in questions if "id" in q}
        for sb in subjects:
            for unit in sb.get("units", []):
                for l in unit.get("lessons", []):
                    self.lessons[l["id"]] = {"id": l["id"], "title": l.get("title", ""), "subject": sb.get("title", ""),
                                             "raw": l, "texts": self._lesson_texts(l)}
        for q in questions:  # الأسئلة نفسها مصدر معرفة: السؤال + الإجابة الصح = جملة معلومة
            if q.get("lesson") in self.lessons:
                self.lessons[q["lesson"]]["texts"].extend(self._question_facts(q))
        # الكلمات اللي أولها واو أصلية: بنعرفها من «ال» + و في نص المنهج (الوحدة، الوطني، الوعي، الوادي)
        for L in self.lessons.values():
            for t in L["texts"]:
                for w in words(t):
                    if w.startswith("الو") and len(w) >= 5:
                        WAW_ROOTS.add(stem(w))
        WAW_ROOTS.discard("")
        _stem_cache.clear()
        self.sentences = []  # (الدرس، الجملة، كلماتها)
        for lid, L in self.lessons.items():
            seen = set()
            for t in L["texts"]:
                for s in split_sentences(t):
                    toks = content_tokens(s)
                    key = " ".join(toks)
                    if len(toks) >= 3 and key not in seen:
                        seen.add(key)
                        self.sentences.append((lid, s, toks))
        df, self.vocab = {}, {}
        for _, _, toks in self.sentences:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
            for t in toks:
                self.vocab[t] = self.vocab.get(t, 0) + 1
        n = len(self.sentences) or 1
        self.idf = {t: math.log((n + 1) / (c + 0.5)) + 1 for t, c in df.items()}
        self.max_idf = math.log(n + 1) + 1
        self.lesson_vocab = {lid: set() for lid in self.lessons}
        for lid, _, toks in self.sentences:
            self.lesson_vocab[lid].update(toks)
        self.gram_index = {}
        for w in self.vocab:
            for g in grams(w):
                self.gram_index.setdefault(g, set()).add(w)

    @staticmethod
    def _lesson_texts(l):
        out = [l.get("title", ""), l.get("summary", "")] + list(l.get("objectives", [])) + list(l.get("keypoints", []))
        for p in l.get("parts", []):
            out.append(p.get("title", ""))
            out += [d.get("text", "") for d in p.get("dialogue", []) if d.get("who") != "student"]
            ex = p.get("example") or []
            for e in (ex if isinstance(ex, list) else [ex]):
                out += [e.get("text", ""), e.get("quote", ""), e.get("caption", "")]
                tb = e.get("table")
                if tb:
                    out += ["، ".join(str(c) for c in row) for row in tb.get("rows", [])]
            t = p.get("task")
            if t:
                out.append(t.get("model", ""))
            for c in p.get("checkpoint", []):
                out.append(c.get("explain", ""))
        return [x for x in out if x]

    @staticmethod
    def _question_facts(q):
        t, text = q.get("type"), q.get("q", "")
        blank = re.compile(r"\.{3,}|…+|_{3,}")
        if t == "essay":
            return [q.get("model", "")] + list(q.get("points", []))
        if t == "mcq":
            ans = q["options"][q["answer"]]
            return [blank.sub(ans, text) if blank.search(text) else text + " " + ans, q.get("explain", "")]
        if t == "fill":
            ans = (q["answer"] if isinstance(q["answer"], list) else [q["answer"]])[0]
            return [blank.sub(ans, text) if blank.search(text) else text + " " + ans, q.get("explain", "")]
        if t == "tf":
            return [text if q.get("answer") else "", q.get("explain", "")]
        if t == "match":
            return ["%s: %s" % (p["a"], p["b"]) for p in q.get("pairs", [])]
        return []

    def w(self, tok):
        base = self.idf.get(tok, self.max_idf)
        return base * 0.3 if tok in LIGHT_WORDS else base

    def nearest_word(self, tok, min_sim=0.55):
        """أقرب كلمة في المنهج لكلمة مكتوبة غلط (بالحروف)"""
        cand = set()
        for g in grams(tok):
            cand |= self.gram_index.get(g, set())
        best, bs = None, min_sim
        for c in cand:
            s = dice(tok, c)
            if s > bs:
                best, bs = c, s
        return best, bs


# =====================================================================
# ٤) تمثيل الكلمات (Word Embeddings) — شبكة Skip-gram مع Negative Sampling
#    بتتعلم إن الكلمات اللي بتيجي في نفس السياق في الدروس معناها قريب
# =====================================================================
def sigmoid(x):
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cosine(a, b):
    na, nb = math.sqrt(dot(a, a)), math.sqrt(dot(b, b))
    return dot(a, b) / (na * nb) if na and nb else 0.0


class WordEmbeddings:
    def __init__(self, dim=16):
        self.dim, self.vec, self._oov = dim, {}, {}

    def train(self, sentences, rng, epochs=4, window=2, neg=3, lr=0.05):
        vocab = sorted({t for s in sentences for t in s})
        idx = {w: i for i, w in enumerate(vocab)}
        freq = [0] * len(vocab)
        for s in sentences:
            for t in s:
                freq[idx[t]] += 1
        table = []
        for i, f in enumerate(freq):
            table += [i] * max(1, int(round(f ** 0.75)))
        W = [[(rng.random() - 0.5) / self.dim for _ in range(self.dim)] for _ in vocab]
        C = [[0.0] * self.dim for _ in vocab]
        total, step = epochs * len(sentences), 0
        for ep in range(epochs):
            order = list(range(len(sentences)))
            rng.shuffle(order)
            for si in order:
                s = [idx[t] for t in sentences[si]]
                a = lr * max(0.1, 1 - step / total)
                step += 1
                for i, center in enumerate(s):
                    wc = W[center]
                    for j in range(max(0, i - window), min(len(s), i + window + 1)):
                        if j == i:
                            continue
                        grad = [0.0] * self.dim
                        targets = [(s[j], 1.0)] + [(table[rng.randrange(len(table))], 0.0) for _ in range(neg)]
                        for tgt, label in targets:
                            ct = C[tgt]
                            g = a * (label - sigmoid(dot(wc, ct)))
                            for k in range(self.dim):
                                grad[k] += g * ct[k]
                                ct[k] += g * wc[k]
                        for k in range(self.dim):
                            wc[k] += grad[k]
        self.vec = {w: W[i] for i, w in enumerate(vocab)}

    def get(self, tok, kb=None):
        v = self.vec.get(tok)
        if v is not None:
            return v
        if tok in self._oov:
            return self._oov[tok]
        v = None
        if kb is not None:  # كلمة جديدة؟ ناخد معنى أقرب كلمة ليها في الحروف
            near, _ = kb.nearest_word(tok)
            v = self.vec.get(near) if near else None
        self._oov[tok] = v
        return v

    def sentence(self, toks, kb):
        acc, tw = [0.0] * self.dim, 0.0
        for t in toks:
            v = self.get(t, kb)
            if v is None:
                continue
            w = kb.w(t)
            nv = math.sqrt(dot(v, v)) or 1.0
            for k in range(self.dim):
                acc[k] += w * v[k] / nv
            tw += w
        return [x / tw for x in acc] if tw else acc

    def neighbours(self, tok, k=3):
        v = self.vec.get(tok)
        if v is None:
            return []
        sims = sorted(((cosine(v, u), w) for w, u in self.vec.items() if w != tok), reverse=True)
        return [w for s, w in sims[:k] if s > 0.6]


# =====================================================================
# ٥) الإدراك: قراءة إجابة الطالب واستخراج الخصائص
# =====================================================================
FEATURE_NAMES = ["تغطية الأفكار", "نسبة الأفكار الموجودة", "أضعف فكرة", "استرجاع الإجابة النموذجية",
                 "القرب في المعنى", "الارتباط بالدرس", "الطول", "إضافة على السؤال",
                 "كلمات من المنهج", "عدم التكرار", "الأرقام والتواريخ", "النفي", "ترتيب الأفكار"]


class Reader:
    FOUND = 0.5

    def __init__(self, kb, emb):
        self.kb, self.emb = kb, emb
        self._svec = {}

    def tok_sim(self, t, a):
        if t == a:
            return 1.0
        if t.isdigit() or a.isdigit():
            return 0.0
        d = dice(t, a)
        if d >= 0.55:
            return min(0.95, d + 0.1)
        if len(t) >= 4 and len(a) >= 4 and (t.startswith(a) or a.startswith(t)):
            return 0.8
        if t in SYN_ID and SYN_ID[t] == SYN_ID.get(a):
            return 0.85
        st, sa = skeleton(t), skeleton(a)
        if st == sa and len(st) >= 3:  # نفس الجذر بصيغة تانية (فعل/اسم، فصحى/عامية)
            return 0.85
        if len(t) >= 3 and len(a) >= 3:  # جذور فيها حرف علة (حماية/تحمي): بنقارن من غير حروف الزيادة
            ct, ca = re.sub("^[تينب]", "", st), re.sub("^[تينب]", "", sa)
            if ct == ca and len(ct) == 2:
                return 0.7
        return 0.0

    def soft_recall(self, ref, ans, ans_set=None):
        if not ref:
            return 1.0
        ans_set = ans_set or set(ans)
        num = den = 0.0
        for t in ref:
            w = self.kb.w(t)
            m = 1.0 if t in ans_set else max([self.tok_sim(t, a) for a in ans_set] or [0.0])
            num += w * m
            den += w
        return num / den if den else 0.0

    def polar_recall(self, ptag, pos, neg):
        """زي soft_recall بس الكلمة المثبتة لازم تقابلها كلمة مثبتة، والمنفية تقابلها منفية"""
        num = den = 0.0
        both = pos | neg
        for t, n in ptag:
            # الفكرة المنفية («عدم التأثر») ممكن تتقال مثبتة («حمى مصر من التأثر») فمش بندقق فيها
            pool = both if n else pos
            w = self.kb.w(t)
            num += w * (1.0 if t in pool else max([self.tok_sim(t, a) for a in pool] or [0.0]))
            den += w
        return num / den if den else 0.0

    def contradicts(self, ptag, seq):
        """الفكرة فيها ناحية من ضدين، والطالب كتب الناحية التانية جنب نفس موضوع الفكرة؟
        مثال: الفكرة «تعزيز الوعي الوطني» والطالب كتب «ضعف الوعي الوطني»"""
        P = {ant_of(t) for t, n in ptag if not n} - {None}
        if not P:
            return False
        topic = [t for t, n in ptag if not ant_of(t)]
        sides = []
        for a, n in seq:
            r = ant_of(a)
            sides.append((r[0], 1 - r[1]) if (r and n) else r)  # «مش ضعيف» = قوي
        if not topic:  # الفكرة كلها كلمة ضد واحدة: أي ضد من غير الناحية الصح يبقى عكس
            have = set(sides) - {None}
            return any((i, 1 - sd) not in P and (i, 1 - sd) in have and (i, sd) not in have for i, sd in P)
        # لكل مكان في كلام الطالب فيه موضوع الفكرة: أقرب كلمة ضد ليه من أنهي ناحية؟
        # «حكومة مركزية ضعيفة والقوة العسكرية» ← «ضعيفة» هي الأقرب لـ«مركزية» فده عكس الفكرة
        # «حاول يضعف المصريين لكن الثورة زادت الوعي» ← «زادت» هي الأقرب لـ«الوعي» فمش عكس
        for k, (a, _) in enumerate(seq):
            if not any(self.tok_sim(t, a) >= 0.8 for t in topic):
                continue
            for i, sd in P:
                if (i, 1 - sd) in P:
                    continue
                # في العربي الصفة بتيجي بعد الموصوف («جيش ضعيف»)، فالكلمة اللي بعد الموضوع أقرب شوية
                dist = lambda j: abs(j - k) + (0.5 if j < k else 0)
                d_bad = min([dist(j) for j, r in enumerate(sides) if r == (i, 1 - sd) and abs(j - k) <= 3] or [99])
                d_ok = min([dist(j) for j, r in enumerate(sides) if r == (i, sd) and abs(j - k) <= 3] or [99])
                if d_bad < d_ok:
                    return True
        return False

    def point_score(self, point, ans_toks, ans_set, ans_sents, pos=None, neg=None, seq=None):
        ptoks = content_tokens(point)
        rec_all = self.soft_recall(ptoks, ans_toks, ans_set)
        flag = ""
        if pos is None:
            rec = rec_all
        else:
            ptag = tagged_tokens(point)
            rec = self.polar_recall(ptag, pos, neg)
            if rec_all >= 0.3 and rec_all - rec >= 0.2 and rec < self.FOUND:
                flag = "neg"       # الطالب ذكر الفكرة بس نفاها
            if self.contradicts(ptag, seq or []):
                rec *= 0.3
                flag = "contra"    # الطالب كتب عكس الفكرة
        best, bs = "", -1.0
        pv = self.emb.sentence(ptoks, self.kb)
        # الاستشهاد: الجزء من كلام الطالب اللي فيه الفكرة (مش الجملة كلها)
        clauses = [c.strip() for s, _ in ans_sents for c in re.split(r"[،,:–]", s) if c.strip()]
        for c in clauses:
            ct = content_tokens(c)
            if not ct:
                continue
            r = self.soft_recall(ptoks, ct, set(ct)) - 0.002 * len(ct)
            if r > bs:
                best, bs = c, r
        sem = max(0.0, cosine(pv, self.emb.sentence([t for s in ans_sents for t in s[1]], self.kb))) if ans_toks else 0
        return min(1.0, 0.85 * rec + 0.15 * sem * rec ** 0.5), best, flag

    def lesson_topic(self, vec, lesson):
        best = 0.0
        for i, (lid, _, toks) in enumerate(self.kb.sentences):
            if lesson and lid != lesson:
                continue
            sv = self._svec.get(i)
            if sv is None:
                sv = self._svec[i] = self.emb.sentence(toks, self.kb)
            best = max(best, cosine(vec, sv))
        return max(0.0, best)

    def features(self, q, answer):
        """q = {q, model, points, lesson} — بيرجع الخصائص + تفاصيل للتفكير"""
        ans_toks = content_tokens(answer)
        ans_set = set(ans_toks)
        ans_sents = [(s, content_tokens(s)) for s in split_sentences(answer)] or [(answer, ans_toks)]
        points = q.get("points") or split_points(q.get("model", ""))
        model_toks = content_tokens(q.get("model", ""))
        q_toks = set(content_tokens(q.get("q", "")))
        tag = tagged_tokens(answer)
        pos, neg = {t for t, n in tag if not n}, {t for t, n in tag if n}
        pscores = [self.point_score(p, ans_toks, ans_set, ans_sents, pos, neg, tag) for p in points]
        sc = [s for s, _, _ in pscores]
        av = self.emb.sentence(ans_toks, self.kb)
        mv = self.emb.sentence(model_toks, self.kb)
        lv = self.kb.lesson_vocab.get(q.get("lesson"), set(self.kb.vocab))
        known = sum(1 for t in ans_toks if t in lv or self.kb.nearest_word(t, 0.6)[0] in lv) / len(ans_toks) if ans_toks else 0
        novelty = sum(1 for t in ans_toks if t not in q_toks) / len(ans_toks) if ans_toks else 0
        uniq = len(ans_set) / len(ans_toks) if ans_toks else 0
        mnum, anum = numbers(q.get("model", "")), numbers(answer)
        num_ok = len(mnum & anum) / len(mnum) if mnum else 1.0
        neg_a = sum(1 for w in words(answer) if is_neg(w))
        neg_m = sum(1 for w in words(q.get("model", "")) if is_neg(w))
        neg_ok = 1.0 - min(1.0, abs(neg_a - neg_m) / 2.0)
        mb = list(zip(model_toks, model_toks[1:]))
        ab = set(zip(ans_toks, ans_toks[1:]))
        order = sum(1 for b in mb if b in ab) / len(mb) if mb else 0
        f = [
            sum(sc) / len(sc),
            sum(1 for s in sc if s >= self.FOUND) / len(sc),
            min(sc),
            self.soft_recall(model_toks, ans_toks, ans_set),
            max(0.0, cosine(av, mv)) if ans_toks else 0.0,
            self.lesson_topic(av, q.get("lesson")) if ans_toks else 0.0,
            min(1.0, len(ans_toks) / max(3.0, 0.5 * len(model_toks))),
            novelty,
            known,
            uniq,
            num_ok if ans_toks else 0.0,
            neg_ok,
            min(1.0, order * 2),
        ]
        info = {"points": points, "pscores": pscores, "n_words": len(words(answer)), "n_sents": len(ans_sents),
                "tokens": ans_toks, "known": known, "novelty": novelty, "uniq": uniq, "topic": f[5]}
        return f, info


# =====================================================================
# ٦) الشبكة العصبية: خلايا عصبية مكتوبة من الصفر
#    الطبقة المخفية tanh — والخلية الأخيرة sigmoid بتطلع رقم من ٠ لـ ١
# =====================================================================
class NeuralNet:
    def __init__(self, n_in=13, n_hidden=10, rng=None):
        rng = rng or random.Random(SEED)
        lim1, lim2 = math.sqrt(6 / (n_in + n_hidden)), math.sqrt(6 / (n_hidden + 1))
        self.W1 = [[rng.uniform(-lim1, lim1) for _ in range(n_in)] for _ in range(n_hidden)]
        self.b1 = [0.0] * n_hidden
        self.W2 = [rng.uniform(-lim2, lim2) for _ in range(n_hidden)]
        self.b2 = 0.0

    def forward(self, x):
        h = [math.tanh(dot(w, x) + b) for w, b in zip(self.W1, self.b1)]
        return sigmoid(dot(self.W2, h) + self.b2), h

    def predict(self, x):
        return self.forward(x)[0]

    def train(self, X, Y, rng, epochs=150, lr=0.08, log=None):
        data = list(zip(X, Y))
        for ep in range(epochs):
            rng.shuffle(data)
            a, loss = lr * (1 - 0.8 * ep / epochs), 0.0
            for x, y in data:
                out, h = self.forward(x)
                err = out - y
                loss += err * err
                d_out = err * out * (1 - out)  # مشتقة الخطأ عند الخلية الأخيرة
                for j in range(len(h)):
                    d_h = d_out * self.W2[j] * (1 - h[j] * h[j])  # الخطأ بيرجع للخلايا المخفية
                    self.W2[j] -= a * d_out * h[j]
                    wj = self.W1[j]
                    for k in range(len(x)):
                        wj[k] -= a * d_h * x[k]
                    self.b1[j] -= a * d_h
                self.b2 -= a * d_out
            if log and (ep % 30 == 0 or ep == epochs - 1):
                log("   دورة %d/%d — متوسط الخطأ %.4f" % (ep + 1, epochs, loss / len(data)))
        return loss / len(data)

    def to_dict(self):
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    @classmethod
    def from_dict(cls, d):
        n = cls(len(d["W1"][0]), len(d["W1"]))
        n.W1, n.b1, n.W2, n.b2 = d["W1"], d["b1"], d["W2"], d["b2"]
        return n


# =====================================================================
# ٧) التدريب: بيولّد إجابات طلاب «افتراضية» من المنهج ويتعلم منها
#    صح كاملة / ناقصة / بأخطاء إملائية / من غير تشكيل / غلط / كلام فاضي
# =====================================================================
AR_LETTERS = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
DIACS = ["\u064e", "\u064f", "\u0650", "\u0652", "\u0651", "\u064b"]


class TrainingData:
    def __init__(self, kb, emb, rng):
        self.kb, self.emb, self.rng = kb, emb, rng

    def items(self):
        """كل الأسئلة اللي ليها إجابة نموذجية في المنهج"""
        out = []
        for q in self.kb.questions.values():
            if q.get("type") == "essay":
                out.append({"q": q["q"], "model": q["model"], "points": q.get("points") or split_points(q["model"]), "lesson": q["lesson"]})
        for lid, L in self.kb.lessons.items():
            for p in L["raw"].get("parts", []):
                t = p.get("task")
                if t and t.get("model"):
                    out.append({"q": " ".join(t.get("steps", [])[-1:]) or p.get("title", ""), "model": t["model"],
                                "points": split_points(t["model"]), "lesson": lid})
            for k in L["raw"].get("keypoints", []):
                pts = split_points(k)
                if len(pts) >= 2:
                    out.append({"q": "وضّح: " + " ".join(k.split()[:5]), "model": k, "points": pts, "lesson": lid})
        return out

    # ---- تشويش الكلام زي ما الطالب ممكن يكتب ----
    def typo(self, w):
        r = self.rng
        if len(w) < 4:
            return w
        i = r.randrange(1, len(w) - 1)
        op = r.random()
        if op < 0.35:
            return w[:i] + w[i + 1:]
        if op < 0.7:
            return w[:i] + r.choice(AR_LETTERS) + w[i + 1:]
        return w[:i - 1] + w[i] + w[i - 1] + w[i + 1:]

    def noisy(self, text, p_typo=0.12, p_drop=0.1, diac=True):
        out = []
        for w in text.split():
            x = self.rng.random()
            if x < p_drop and len(out) > 2:
                continue
            if x < p_drop + p_typo:
                w = self.typo(w)
            if diac and self.rng.random() < 0.2:
                w = "".join(c + (self.rng.choice(DIACS) if self.rng.random() < 0.3 else "") for c in w)
            out.append(w)
        t = " ".join(out)
        if self.rng.random() < 0.5:
            t = t.replace("أ", "ا").replace("إ", "ا").replace("ة", "ه").replace("ى", "ي")
        return t

    def paraphrase(self, text):
        out = []
        for w in text.split():
            st = stem(normalize(w))
            nb = self.emb.neighbours(st, 2) if self.rng.random() < 0.25 else []
            out.append(self.rng.choice(nb) if nb else w)
        return " ".join(out)

    def samples(self):
        r, items = self.rng, self.items()
        all_sents = self.kb.sentences
        S = []
        for it in items:
            pts, n, model = it["points"], len(it["points"]), it["model"]
            same = [x for x in items if x["lesson"] == it["lesson"] and x is not it and x["model"] != model]
            other = [x for x in items if x["lesson"] != it["lesson"]]
            S.append((it, model, 1.0))
            S.append((it, self.noisy(model), 0.95))
            S.append((it, self.noisy(model, 0.2, 0.15), 0.9))
            S.append((it, "، و".join(r.sample(pts, n)), 0.95))
            S.append((it, self.paraphrase(model), 0.85))
            for _ in range(3 if n > 2 else 1):
                if n > 1:
                    k = r.randint(1, n - 1)
                    S.append((it, self.noisy("، ".join(r.sample(pts, k)), 0.08, 0.05), 0.95 * k / n))
            if same:
                S.append((it, r.choice(same)["model"], 0.05))
            if other:
                S.append((it, r.choice(other)["model"], 0.0))
                S.append((it, self.noisy(r.choice(other)["model"]), 0.0))
            S.append((it, it["q"], 0.03))
            S.append((it, it["q"] + " " + it["q"], 0.03))
            S.append((it, " ".join("".join(r.choice(AR_LETTERS) for _ in range(r.randint(3, 7))) for _ in range(r.randint(5, 15))), 0.0))
            ws = [w for w in model.split() if normalize(w) not in STOPWORDS]
            if ws:
                S.append((it, " ".join([r.choice(ws)] * r.randint(5, 12)), 0.02))
                salad = r.sample(ws, min(len(ws), 5))
                S.append((it, " ".join(salad), 0.3 * min(1.0, len(salad) / max(4, len(ws) / 2))))
            S.append((it, r.choice(["لا أعرف", "مش عارف الإجابة", "معرفش والله", "مش فاكر"]), 0.0))
            neg = re.sub(r"(^|\s)(هو|هي|تعد|يعد|كان|كانت|جعل|جعلت|أصبحت)\s", r"\1\2 لا ", model, count=2)
            if neg == model:
                neg = "ليس صحيحًا أن " + model
            S.append((it, neg, 0.3))
            unrelated = [s for lid, s, _ in all_sents if lid == it["lesson"]]
            if unrelated:
                S.append((it, "، ".join(r.sample(unrelated, min(2, len(unrelated)))), 0.1))
        return S


# =====================================================================
# ٨) التفكير: المصحح بيراجع الإجابة خطوة خطوة ويدّي الدرجة
# =====================================================================
class Thinker:
    def __init__(self, kb, emb, net):
        self.kb, self.emb, self.net = kb, emb, net
        self.reader = Reader(kb, emb)

    def lesson_evidence(self, point, lesson):
        """بيدوّر في الدرس على الجملة اللي بتشرح الفكرة الناقصة"""
        pt = content_tokens(point)
        best, bs = None, 0.0
        for lid, s, toks in self.kb.sentences:
            if lid != lesson or toks == pt:  # بندوّر في شرح الدرس، مش في نص الفكرة نفسها
                continue
            cl = [c.strip(" «»") for c in re.split(r"[،,:]", s) if c.strip()]
            for i in range(len(cl)):  # جزء أو جزئين متتاليين من الجملة
                for part in (cl[i], "، ".join(cl[i:i + 2])):
                    ct = content_tokens(part)
                    if len(ct) < 2 or (len(pt) <= 3 and len(ct) < len(pt) + 2):  # الفكرة القصيرة محتاجة استشهاد فيه شرح
                        continue
                    sc = self.reader.soft_recall(pt, ct) - 0.015 * len(ct)
                    if sc > bs:
                        best, bs = part, sc
        return best if bs > 0.45 else None

    @staticmethod
    def short(s, n=90):
        s = re.sub(r"\s+", " ", s).strip()
        return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."

    def grade_essay(self, q, answer):
        marks = float(q.get("marks", 4))
        lesson = self.kb.lessons.get(q.get("lesson"), {})
        answer = str(answer or "").strip()[:4000]
        th = []
        res = {"marks": marks, "score": 0.0, "ratio": 0.0, "confidence": "عالية", "found": [], "partial": [], "missing": [],
               "thoughts": th, "feedback": "", "model": q.get("model", "")}
        n_words = len(words(answer))
        th.append("قريت الإجابة: %s كلمة." % to_ar(n_words))
        nq = normalize(answer)
        if len(content_tokens(answer)) < 2 or any(nq == d or (nq.startswith(d) and n_words <= 5) for d in DONT_KNOW):
            th.append("الإجابة فاضية أو مفيهاش معلومات، فمفيش حاجة تتصحح.")
            res["missing"] = list(q.get("points", []))
            res["feedback"] = "مكتبتش إجابة. راجع الإجابة النموذجية تحت وحاول تكتبها بأسلوبك."
            return res
        f, info = self.reader.features(q, answer)
        pts = info["points"]
        th.append("السؤال من درس «%s»، ومطلوب فيه %s أفكار أساسية." % (lesson.get("title", ""), to_ar(len(pts))))
        flagged = []
        for (sc, where, flag), p in zip(info["pscores"], pts):
            if flag:
                flagged.append(flag)
                res["missing"].append(p)
                th.append(("فكرة «%s»: اتذكرت بس منفية في كلامك: «%s» — فمش هتتحسب." if flag == "neg" else
                           "فكرة «%s»: كلامك بيقول عكسها: «%s» — فمش هتتحسب.") % (p, self.short(where)))
            elif sc >= Reader.FOUND:
                res["found"].append(p)
                th.append("فكرة «%s»: موجودة في كلامك: «%s»" % (p, self.short(where)))
            elif sc >= 0.3 and where:
                res["partial"].append(p)
                th.append("فكرة «%s»: موجودة جزئيًا في كلامك: «%s» — بس ناقصها تفاصيل." % (p, self.short(where)))
            else:
                res["missing"].append(p)
                ev = self.lesson_evidence(p, q.get("lesson"))
                th.append("فكرة «%s»: ناقصة.%s" % (p, (" الدرس بيقول: «%s»" % self.short(ev)) if ev else ""))
        p_nn = self.net.predict(f)
        frac = (len(res["found"]) + 0.5 * len(res["partial"])) / len(pts)  # الفكرة الناقصة جزئيًا بنص درجة
        ratio = max(0.55 * p_nn + 0.45 * frac, frac - 0.05)  # لو كل الأفكار موجودة الدرجة بتقرب من النهائية
        caps = []
        if info["novelty"] < 0.3:
            caps.append((0.05, "أغلب الإجابة منقولة من نص السؤال نفسه."))
        if info["uniq"] < 0.4 and len(info["tokens"]) >= 5:
            caps.append((0.15, "في كلام متكرر كتير في الإجابة."))
        if info["known"] < 0.15 or (info["known"] < 0.35 and max(sc for sc, _, _ in info["pscores"]) < 0.3):
            caps.append((0.1, "أغلب الكلمات مش من المنهج أو مش مفهومة."))
        if f[5] < 0.35 and f[0] < 0.3:
            caps.append((0.15, "الإجابة بعيدة عن موضوع الدرس."))
        # رص كلمات: كلمات ورا بعض من غير أي أداة ربط، أو قايمة كل بند فيها كلمة واحدة
        items = [x for x in re.split(r"[.!؟?؛;،,:\n\-–/]+", answer) if words(x)]
        ws_all = words(answer)
        glue = sum(1 for w in ws_all if w in STOPWORDS or w in NEGATIONS or (w.startswith("و") and len(w) >= 3))
        if (len(items) == 1 and len(ws_all) >= 4 and glue == 0) or (len(items) >= 4 and all(len(words(x)) == 1 for x in items)):
            caps.append((0.25, "الإجابة كلمات مرصوصة من غير شرح."))
        for cap, why in caps:
            th.append("انتباه: " + why)
            ratio = min(ratio, cap)
        th.append("مدى ارتباط الإجابة بالدرس: %s٪ — والقرب في المعنى من الإجابة النموذجية: %s٪."
                  % (to_ar(round(f[5] * 100)), to_ar(round(f[4] * 100))))
        agree = 1 - abs(p_nn - frac)
        conf = "عالية" if agree >= 0.75 or p_nn < 0.15 or p_nn > 0.85 else ("متوسطة" if agree >= 0.5 else "منخفضة")
        th.append("الشبكة العصبية قدّرت جودة الإجابة بـ %s٪ (الثقة: %s)." % (to_ar(round(p_nn * 100)), conf))
        if frac == 0 and not caps:
            th.append("مفيش ولا فكرة من الأفكار المطلوبة في الإجابة.")
            ratio = 0.0
        ratio = max(0.0, min(1.0, ratio))
        score = (math.floor if caps else round)(ratio * marks * 2) / 2
        th.append("الدرجة النهائية: %s من %s." % (to_ar(score), to_ar(marks)))
        # هل الدرجة دي محتاجة عين المعلم؟
        review = ""
        if flagged:
            review = "المصحح لقى في إجابتك نفي أو عكس لفكرة من الأفكار المطلوبة فمحسبهاش. لو قصدك كان غير كده، اعرض إجابتك على المعلم."
        elif not caps and n_words >= 12 and ratio <= 0.4 and info["topic"] >= 0.35 and info["known"] >= 0.15:
            review = "إجابتك طويلة ومتعلقة بالدرس، بس مكتوبة بكلمات بعيدة عن الإجابة النموذجية، فالمصحح مش متأكد من درجتها. الدرجة دي مبدئية، واعرضها على المعلم يراجعها."
        elif conf == "منخفضة":
            review = "ثقة المصحح في الدرجة دي منخفضة، فالأحسن المعلم يراجعها."
        if review:
            th.append("قرار: الإجابة دي محتاجة مراجعة المعلم.")
        res.update(score=score, ratio=round(ratio, 3), confidence=conf, nn=round(p_nn, 3), review=review)
        if score >= marks:
            res["feedback"] = "ممتاز! إجابتك فيها كل الأفكار المطلوبة."
        elif (res["found"] or res["partial"]) and not caps:
            res["feedback"] = ("إجابة كويسة، بس ناقصها: " if res["found"] else "بدأت صح، بس ناقصك: ") + "، ".join(res["partial"] + res["missing"]) + "."
        elif caps:
            res["feedback"] = caps[0][1] + " اكتب الأفكار المطلوبة بأسلوبك."
        elif flagged:
            res["feedback"] = "إجابتك ذكرت الأفكار بس نفتها أو قالت عكسها. راجع الجزء ده في الدرس."
        else:
            res["feedback"] = "الإجابة مش فيها الأفكار المطلوبة. راجع الجزء ده في الدرس."
        return res

    # ---- الأسئلة الموضوعية ----
    @staticmethod
    def grade_objective(q, value):
        t = q.get("type")
        marks = MARKS.get(t, 1)
        out = {"marks": marks, "score": 0.0, "given": "لم تُجب", "correct_text": ""}
        if t == "mcq":
            out["correct_text"] = q["options"][q["answer"]]
            if isinstance(value, int) and 0 <= value < len(q["options"]):
                out["given"] = q["options"][value]
                out["score"] = float(marks) if value == q["answer"] else 0.0
        elif t == "tf":
            out["correct_text"] = "صح" if q["answer"] else "خطأ"
            if isinstance(value, bool):
                out["given"] = "صح" if value else "خطأ"
                out["score"] = float(marks) if value == bool(q["answer"]) else 0.0
        elif t == "fill":
            ans = q["answer"] if isinstance(q["answer"], list) else [q["answer"]]
            out["correct_text"] = ans[0]
            if isinstance(value, str) and value.strip():
                out["given"] = value
                out["score"] = float(marks) if normalize(value) in [normalize(a) for a in ans] else 0.0
        elif t == "match":
            pairs = q["pairs"]
            out["correct_text"] = " ، ".join("%s: %s" % (p["a"], p["b"]) for p in pairs)
            if isinstance(value, dict) and value:
                good, given = 0, []
                for i, p in enumerate(pairs):
                    v = value.get(str(i))
                    try:
                        j = int(v)
                    except (TypeError, ValueError):
                        j = -1
                    good += 1 if j == i else 0
                    given.append("%s: %s" % (p["a"], pairs[j]["b"] if 0 <= j < len(pairs) else "—"))
                out["given"] = " ، ".join(given)
                out["score"] = round(good / len(pairs) * marks * 2) / 2
                out["pairs_ok"] = good
        return out

    # ---- «تحقق من إجابتك» في التطبيق العملي ----
    def check_task(self, lesson_id, part_i, text):
        L = self.kb.lessons.get(lesson_id)
        if not L:
            return {"error": "الدرس مش موجود"}
        parts = L["raw"].get("parts", [])
        if not (0 <= part_i < len(parts)) or not parts[part_i].get("task"):
            return {"error": "الجزء ده مفيهوش تطبيق"}
        t = parts[part_i]["task"]
        q = {"q": " ".join(t.get("steps", [])), "model": t.get("model", ""), "points": split_points(t.get("model", ""), 1),
             "lesson": lesson_id, "marks": 1}
        toks = set(content_tokens(text))
        missing_kw = []
        for kw in t.get("check", []):  # الكلمات الأساسية لازم تكون موجودة (حتى لو مكتوبة من غير همزة أو تشكيل)
            kt = content_tokens(kw)
            if not kt or self.reader.soft_recall(kt, list(toks), toks) < 0.75:
                missing_kw.append(kw)
        g = self.grade_essay(q, text)
        junk = any(("منقولة" in x or "متكرر" in x or "مش مفهومة" in x) for x in g["thoughts"])
        ok = bool(text.strip()) and not missing_kw and not junk and g["ratio"] >= 0.2
        return {"ok": ok, "missing": missing_kw, "extra": [] if ok else g["missing"][:3], "ratio": g["ratio"],
                "thoughts": g["thoughts"], "feedback": ("لقد فعلتها! إجابتك فيها كل المطلوب." if ok else
                                                         ("لسه ناقص: " + "، ".join(missing_kw) if missing_kw else g["feedback"]))}


# =====================================================================
# تجميع الذكاء: قراءة المنهج ← المعرفة ← تمثيل الكلمات ← الشبكة
# =====================================================================
class Grader:
    def __init__(self, retrain=False, data=None, cached=None):
        t0 = time.time()
        if data is None:
            subjects, questions, self.site, digest = read_platform()
        else:  # جوه المتصفح: المنهج جاي من المنصة مباشرة
            subjects, questions, self.site = data["subjects"], data["questions"], data.get("site", "المنصة")
            digest = data_digest(subjects, questions)
        self.kb = KnowledgeBase(subjects, questions)
        say("  قريت المنهج: %d درس، %d سؤال، %d جملة، %d كلمة مختلفة."
            % (len(self.kb.lessons), len(self.kb.questions), len(self.kb.sentences), len(self.kb.vocab)))
        key = "%s-%s-%s" % (VERSION, digest, code_digest())
        if cached is not None:
            if retrain or not isinstance(cached, dict) or cached.get("key") != key:
                cached = None
        elif not retrain and data is None and os.path.exists(MODEL_FILE):
            try:
                with open(MODEL_FILE, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("key") != key:
                    say("  المنهج اتغير — هعيد التدريب.")
                    cached = None
            except Exception:
                cached = None
        self.emb = WordEmbeddings(16)
        self.trained, self.blob = False, None
        if cached:
            self.emb.vec = cached["emb"]
            self.net = NeuralNet.from_dict(cached["net"])
            say("  حمّلت الشبكة المتدربة.")
        else:
            rng = random.Random(SEED)
            say("  بتعلّم معاني الكلمات من الدروس (Skip-gram)...")
            self.emb.train([toks for _, _, toks in self.kb.sentences], rng)
            say("  بولّد إجابات تدريب من المنهج...")
            reader = Reader(self.kb, self.emb)
            S = TrainingData(self.kb, self.emb, rng).samples()
            X, Y = [], []
            for it, ans, y in S:
                X.append(reader.features(it, ans)[0])
                Y.append(y)
            say("  بدرّب الشبكة العصبية على %d إجابة..." % len(X))
            self.net = NeuralNet(len(FEATURE_NAMES), 10, rng)
            self.net.train(X, Y, rng, log=say)
            self.trained = True
            self.blob = {"key": key, "emb": {w: [round(x, 5) for x in v] for w, v in self.emb.vec.items()},
                         "net": self.net.to_dict(), "trained_on": len(X)}
            if data is None:
                try:
                    with open(MODEL_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.blob, f, ensure_ascii=False)
                except OSError:
                    pass
        self.thinker = Thinker(self.kb, self.emb, self.net)
        say("  المصحح الذكي جاهز (%.1f ثانية)." % (time.time() - t0))


# =====================================================================
# ٩) المراجعة: طابور بيصحح الامتحانات وبيحفظ النتايج
# =====================================================================
class ReviewDesk:
    def __init__(self, grader):
        self.g = grader
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.subs = {}
        if os.path.exists(RESULTS_FILE):
            try:
                with open(RESULTS_FILE, encoding="utf-8") as f:
                    self.subs = json.load(f)
            except Exception:
                self.subs = {}
        for sid, s in self.subs.items():  # امتحانات ما خلصتش قبل ما البرنامج يتقفل
            if s.get("status") in ("queued", "reviewing"):
                s["status"], s["progress"] = "queued", {"done": 0, "total": len(s.get("answers", []))}
                self.q.put(sid)
        threading.Thread(target=self.worker, daemon=True).start()

    def save(self):
        tmp = RESULTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.subs, f, ensure_ascii=False)
        os.replace(tmp, RESULTS_FILE)

    def submit(self, data):
        lesson = str(data.get("lesson", ""))
        if lesson not in self.g.kb.lessons:
            raise ValueError("الدرس مش موجود")
        answers = []
        for a in data.get("answers", [])[:200]:
            qid = str(a.get("id", ""))
            if qid in self.g.kb.questions and self.g.kb.questions[qid].get("lesson") == lesson:
                v = a.get("value")
                if isinstance(v, str):
                    v = v[:4000]
                answers.append({"id": qid, "value": v})
        if not answers:
            raise ValueError("مفيش إجابات")
        sid = uuid.uuid4().hex[:12]
        with self.lock:
            self.subs[sid] = {"id": sid, "lesson": lesson, "user": str(data.get("user", ""))[:60],
                              "created": time.time(), "status": "queued", "answers": answers,
                              "progress": {"done": 0, "total": len(answers)}}
            self.save()
        self.q.put(sid)
        return sid

    def public(self, sid):
        with self.lock:
            s = self.subs.get(sid)
            if not s:
                return None
            out = {k: s[k] for k in ("id", "lesson", "status", "progress", "created") if k in s}
            if s["status"] == "queued":
                ahead = [x for x in self.subs.values() if x["status"] in ("queued", "reviewing") and x["created"] < s["created"]]
                out["ahead"] = len(ahead)
            if "result" in s:
                out["result"] = s["result"]
            return out

    def worker(self):
        while True:
            sid = self.q.get()
            try:
                self.review(sid)
            except Exception as e:  # لو حصل خطأ غير متوقع، الامتحان ما يضيعش
                with self.lock:
                    if sid in self.subs:
                        self.subs[sid]["status"] = "error"
                        self.subs[sid]["error"] = str(e)
                        self.save()
                say("  خطأ أثناء التصحيح: %s" % e)

    def review(self, sid):
        with self.lock:
            s = self.subs.get(sid)
            if not s:
                return
            s["status"] = "reviewing"
            answers = list(s["answers"])
        kb, th = self.g.kb, self.g.thinker
        items, got, total = [], 0.0, 0.0
        for n, a in enumerate(answers):
            q = kb.questions[a["id"]]
            time.sleep(REVIEW_SECONDS_ESSAY if q.get("type") == "essay" else REVIEW_SECONDS_OBJECTIVE)
            item = grade_item(th, q, a["value"])
            got += item["score"]
            total += item["marks"]
            items.append(item)
            with self.lock:
                s["progress"] = {"done": n + 1, "total": len(answers), "current": q["type"]}
        percent = round(got / total * 100) if total else 0
        with self.lock:
            s["result"] = {"score": got, "total": total, "percent": percent, "passed": percent >= PASS_PERCENT,
                           "items": items, "reviewed": time.time(), "reviewer": "المصحح الذكي " + VERSION}
            s["status"] = "done"
            self.save()
        say("  خلصت تصحيح امتحان %s: %s من %s (%d٪)" % (sid, got, total, percent))


def grade_item(th, q, value):
    """تصحيح سؤال واحد — بيرجّع كل تفاصيل المراجعة"""
    item = {"id": q["id"], "type": q["type"], "q": q["q"], "explain": q.get("explain", "")}
    if q.get("type") == "essay":
        item.update(th.grade_essay(q, value if isinstance(value, str) else ""))
        item["given"] = value if isinstance(value, str) and value.strip() else "لم تُجب"
    else:
        item.update(th.grade_objective(q, value))
    item["state"] = "correct" if item["score"] >= item["marks"] else ("partial" if item["score"] > 0 else
                                                                       ("empty" if item["given"] == "لم تُجب" else "wrong"))
    return item


# =====================================================================
# المصحح جوه المتصفح (Pyodide): المنصة بتنادي الدوال دي مباشرة من غير سيرفر
# =====================================================================
_BR = {}


def browser_start(data_json, model_json=""):
    """بيجهّز المصحح: بياخد المنهج من المنصة، والشبكة المتدربة لو محفوظة في المتصفح"""
    cached = None
    if model_json:
        try:
            cached = json.loads(model_json)
        except ValueError:
            cached = None
    g = Grader(data=json.loads(data_json), cached=cached)
    _BR["g"] = g
    return json.dumps({"ok": True, "trained": g.trained, "model": g.blob}, ensure_ascii=False)


def browser_grade(qid, value_json):
    g = _BR["g"]
    q = g.kb.questions.get(str(qid))
    if not q:
        return json.dumps({"error": "السؤال مش موجود"}, ensure_ascii=False)
    return json.dumps(grade_item(g.thinker, q, json.loads(value_json)), ensure_ascii=False)


def browser_check(lesson, part, text):
    return json.dumps(_BR["g"].thinker.check_task(str(lesson), int(part), str(text)[:4000]), ensure_ascii=False)


# =====================================================================
# ١٠) الخادم: بيعرض المنصة وبيستقبل الإجابات
# =====================================================================
class Handler(SimpleHTTPRequestHandler):
    desk = None
    grader = None

    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 300000:
            raise ValueError("الطلب كبير جدًا")
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def api_path(self):
        p = self.path.split("?")[0]
        k = p.find("/api/")
        return p[k + 5:] if k >= 0 else None

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        api = self.api_path()
        if api is None:
            if os.path.basename(self.path.split("?")[0]).startswith("ai_results"):
                return self.send_error(404)
            return super().do_GET()
        if api == "ping":
            return self.send_json({"ok": True, "name": "المصحح الذكي", "version": VERSION})
        if api.startswith("result/"):
            r = self.desk.public(api[7:])
            return self.send_json(r) if r else self.send_json({"error": "الامتحان ده مش موجود"}, 404)
        self.send_json({"error": "مش موجود"}, 404)

    def do_POST(self):
        api = self.api_path()
        try:
            data = self.read_json()
            if api == "submit":
                sid = self.desk.submit(data)
                say("  وصل امتحان جديد (%s) — %d سؤال." % (sid, len(data.get("answers", []))))
                return self.send_json({"id": sid, "status": "queued"})
            if api == "check":
                r = self.grader.thinker.check_task(str(data.get("lesson")), int(data.get("part", -1)), str(data.get("text", ""))[:4000])
                return self.send_json(r, 400 if "error" in r else 200)
            self.send_json({"error": "مش موجود"}, 404)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            self.send_json({"error": str(e)}, 400)


# =====================================================================
# تجربة سريعة للمصحح (python ai_grader.py --test)
# =====================================================================
def self_test(grader):
    th = grader.thinker
    for q in [q for q in grader.kb.questions.values() if q.get("type") == "essay"]:
        say("\n" + "=" * 70 + "\nسؤال %s: %s" % (q["id"], q["q"]))
        other = next(x for x in grader.kb.questions.values() if x.get("type") == "essay" and x["id"] != q["id"])
        tests = [
            ("الإجابة النموذجية", q["model"]),
            ("من غير همزات ولا تاء مربوطة", q["model"].replace("أ", "ا").replace("إ", "ا").replace("ة", "ه")),
            ("بتشكيل", "".join(c + ("\u064e" if c in "بتدلمنر" else "") for c in q["model"])),
            ("نص الأفكار بس", "، ".join(q["points"][: max(1, len(q["points"]) // 2)])),
            ("منقول من السؤال", q["q"]),
            ("إجابة سؤال تاني", other["model"]),
            ("كلام فاضي", "الدولة الدولة الدولة الدولة مصر مصر مصر"),
            ("حروف عشوائية", "سيبس بتنتسيب شسيبت لبسل منتلب"),
            ("لا أعرف", "مش عارف"),
        ]
        for name, ans in tests:
            r = th.grade_essay(q, ans)
            say("  %-28s ← %s / %s  (الشبكة %s، الثقة %s)" % (name, r["score"], r["marks"], r.get("nn", "-"), r["confidence"]))
    q = next(q for q in grader.kb.questions.values() if q.get("type") == "essay")
    say("\nخطوات تفكير المصحح في إجابة ناقصة:")
    for t in th.grade_essay(q, "، ".join(q["points"][:3]))["thoughts"]:
        say("   - " + t)


def main():
    args = sys.argv[1:]
    say("=" * 60 + "\n  المصحح الذكي — منصة التعلم التفاعلي\n" + "=" * 60)
    grader = Grader(retrain="--retrain" in args)
    if "--test" in args:
        return self_test(grader)
    Handler.grader = grader
    Handler.desk = ReviewDesk(grader)
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        say("\n  البورت %d مستخدم — غالبًا المصحح شغال بالفعل في نافذة تانية." % PORT)
        say("  افتح: http://localhost:%d/index.html" % PORT)
        return
    url = "http://localhost:%d/index.html" % PORT
    if ONLINE:
        say("\n  المنصة شغالة أونلاين على البورت %d — افتح لينك الموقع من صفحة الاستضافة.\n" % PORT)
    else:
        say("\n  المنصة شغالة على: %s\n  سيب النافذة دي مفتوحة طول ما بتستخدم المنصة. (Ctrl+C للإيقاف)\n" % url)
    if "--no-browser" not in args and not ONLINE:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        say("\n  تم إيقاف المصحح. مع السلامة!")


if __name__ == "__main__" and not BROWSER:
    try:
        main()
    except Exception as err:  # عشان لو اتفتح بدبل كليك، النافذة ما تتقفلش قبل ما الخطأ يتقري
        say("\n  حصل خطأ: %s" % err)
        try:
            input("  اضغط Enter للخروج...")
        except EOFError:
            pass
# ==PY-END==
r"""*/}).toString();
// """
