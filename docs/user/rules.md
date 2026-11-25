# Description patterns

Patterns look for a match in the first 256 characters of a transaction
description. Holdfast collapses whitespace in the description and matches
without regard to case.

Use plain text to find a word, or use RE2 pattern syntax for more control.

- Use `AMZN|AMAZON` to find either spelling.
- Use `^` and `$` to match the start and end of the description.
- Use `[0-9]` for one digit or `\d{4}` for four digits.
- Use `\.` to find a literal dot. A bare dot matches any character except a newline.
- Use `\pL` for a Unicode letter or `[[:alpha:]]` for an ASCII letter.

The shortcuts `\d`, `\w`, `\s`, and `\b` use ASCII definitions. For example,
`\b` treats `é` as a non-word character. Repeat counts on classes add up to at most 8 (`\d{3}-\d{4}` uses 7), and a pattern can have at
most 24 dots, classes and alternations in a row, counting each repeat. Counts on plain
text such as `\*{8}\d{4}` are free, and patterns of plain text skip the speed check. A plain-text pattern has no `.`, class, `|`,
`*`, `+`, `?` or `{n}`. Inline flags such as `(?i)` or `(?s)` aren't supported, because matching is
always case-insensitive. Holdfast also normalizes descriptions and patterns to NFKC before matching, so
the Kelvin sign matches as `K`.
RE2 does not support backreferences or lookahead and lookbehind.

Patterns must be 1 to 128 characters long. Holdfast refuses patterns that
match an empty string, limits the number of character classes and checks
how quickly each new pattern searches sample descriptions. A pattern that
takes too long can be refused because rules run across many transactions.

## Supported syntax

Holdfast reads patterns with a strict grammar and refuses anything else, before the engine sees it.

- Plain characters, and these escaped: `\. \* \+ \? \( \) \[ \] \{ \} \| \\ \^ \$ \- \/ \#`.
- `\d \D \w \W \s \S \b \B`, `\xHH`, `\x{H..H}`, `\pL` and `\p{Name}`.
- Classes `[...]` and `[^...]` with plain characters, ranges such as `a-z` and the escapes above. No nested
  `[`, so no `[:alpha:]`. Write a literal `]` as `\]`.
- Groups `(...)` and `(?:...)`, alternation `|`, `*`, `+`, `?` (and a lazy `?` after them),
  `{n}`, `{n,}` and `{n,m}`, anchors `^` and `$`, and `.`.
- Not supported: inline flags such as `(?i)`, named groups, `\Q...\E`, octal and control escapes.
