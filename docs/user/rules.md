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
`\b` treats `é` as a non-word character. Repeat counts can go up to 1,000.
RE2 does not support backreferences or lookahead and lookbehind.

Patterns must be 1 to 128 characters long. Holdfast refuses patterns that
match an empty string, limits the number of character classes and checks
how quickly each new pattern searches sample descriptions. A pattern that
takes too long can be refused because rules run across many transactions.
