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
