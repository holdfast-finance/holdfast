"""Synthetic ADR 0024 rule-pattern regression fixtures."""

REQUIRED_REJECTIONS = (
    "[AB]*A[ABC]{20}D",
    "A[ABC]{120}D",
    ".*A.{30}B.{30}D",
    "(?:[J][K][V][Q])?[XY]*X[XYZ]{20}W",
    r"\d*[0-4]\d{20}#",
    r"[^5-9]\d{20}#",
    r"\w*Q\w{20}#",
    "[[:digit:]]*1[[:digit:]]{20}#",
    r"(?:0123456789ABCDEFGHIJKLMNOPRSTUVW)?\w*Q\w{20}#",
    r"\w*QJ\w{20}#",
    "[A-Z]*XZ[A-Z]{20}#",
    ".*QJ.{20}#",
    "(?:X|Y)*X(?:X|Y|Z){20}W",
    "(?:1|2)*1(?:1|2|3){20}#",
    "(?:X|Y)*X(?:X|Y|Z){120}W",
    "(?:Q|J)*Q(?:Q|J|K){30}J(?:Q|J|K){30}W",
    r"[^\x00-\x7F]*É[^\x00-\x7F]{20}Ω",
    "[^ -~]*É[^ -~]{20}#",
    r"(?:\p{Greek}|\p{Cyrillic})*\p{Greek}(?:\p{Greek}|\p{Cyrillic}){40}#",
    "(?:[q]|[j])*[q](?:[q]|[j]|[k]){90}[w]",
    "(?:[x]|[y])*[x](?:[x]|[y]|[z]){20}[w]",
    "(?:ß|X)*ß(?:ß|X|Y){90}W",
    r"[\x{10D0}-\x{10EF}]*[\x{10D0}-\x{10DF}][\x{10D0}-\x{10EF}]{16}#",
    r"[\x{10D0}-\x{10EF}]*[\x{10D0}-\x{10DF}][\x{10D0}-\x{10EF}]{16}[\x{10D0}-\x{10EF}]{16}#",
    r"(?:[\x{100}-\x{17F}]|[\x{180}-\x{24F}])*[\x{100}-\x{17F}]"
    r"(?:[\x{100}-\x{17F}]|[\x{180}-\x{24F}]){16}#",
    r"[\x{105}\x{142}]*\x{105}[\x{105}\x{142}\x{17C}]{16}#",
    "[W]{8}[W]{8}",
    "QJV.*X" + "." * 121 + "W",
    "KZ.*X" + "." * 122 + "W",
    "QJV[XY]*X" + "[XY]" * 25 + "W",
    r"(?-i:\x{2126})Ω{200}#",
    r"(?-i)\x{212A}(?i)K{200}#",
    "(?-i:\u2126)" + "Ω" * 100 + "#",
)

# The four escape-only-class patterns again at a total count of 8, which the repetition
# budget allows. The probe must refuse these on its own.
PROBE_ONLY_VARIANTS = (
    r"[\x{10D0}-\x{10EF}]*[\x{10D0}-\x{10DF}][\x{10D0}-\x{10EF}]{8}#",
    r"[\x{10D0}-\x{10EF}]*[\x{10D0}-\x{10DF}][\x{10D0}-\x{10EF}]{4}[\x{10D0}-\x{10EF}]{4}#",
    r"(?:[\x{100}-\x{17F}]|[\x{180}-\x{24F}])*[\x{100}-\x{17F}]"
    r"(?:[\x{100}-\x{17F}]|[\x{180}-\x{24F}]){8}#",
    r"[\x{105}\x{142}]*\x{105}[\x{105}\x{142}\x{17C}]{8}#",
)

# Hand-written repetition after a literal prefix, inside the class-position budget of 24.
# The probe must refuse these on its own.
PROBE_ONLY_VARIANTS += (
    "QJV.*X" + "." * 20 + "W",
    "KZ.*X" + "." * 20 + "W",
    "QJV.*X" + "." * 21 + "W",
    "QJV.*X" + "[^#]" * 20 + "#",
    "QJV[XY]*X" + "[XY]" * 20 + "W",
)

# Required rejections the repetition budget alone refuses (nothing for a probe to sample).
BUDGET_ONLY = ("[W]{8}[W]{8}",)

# Realistic patterns that must stay valid: masked accounts and literal-only counts.
MUST_ACCEPT = (
    r"X{8}\d{4}",
    r"\*{8}\d{4}",
    r"\*{12}\d{4}",
    r"(?:AB){12}\d{4}",
    r"XXXX\d{4}",
    r"ACCT [X*]{4}\d{4}",
    "A" * 128,
)

CORPUS = (
    *REQUIRED_REJECTIONS,
    "A?A?A?A?A*" + "A" * 117 + "B",
    "(x+x+)+y",
    "(?:" + "".join(f"[{char}]" for char in "JKLMNPQRSTUVW") + ")?[XY]*X[XYZ]{20}W",
    "(?:[J][K][V][Q][R])?[AB]*A[ABC]{20}D",
    "[A-Z ]*E[A-Z ]{20}Q",
    "[A-Z0-9 ]*X[A-Z0-9 ]{14}Q",
    "A" * 128,
    r"AMZN MKTP|AMAZON\.COM",
    "UBER (EATS|TRIP)",
    "^[A-Z]+ [0-9]{4}$",
    r"\d{3}-\d{4}",
    "[^0-9]+ PAYMENT",
    r"(?:VENMO|ZELLE|PAYPAL) \*?[A-Z ]+",
    "[^A-W]{1}[XYZ]{120}#",
    *(f"((AB|ABA|ABAB){{{count}}})+C" for count in (2, 4, 8)),
    *(f"[{a}{b}]*{a}[{a}{b}{c}]{{20}}{d}" for a, b, c, d in ("XYZW", "PQRS", "JKLM")),
)
