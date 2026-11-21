"""Parse CSV exports with explicit column mappings and post-transaction running balances."""

from __future__ import annotations

import codecs
import csv
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from io import StringIO
from typing import Literal

from app.domain.money import ZERO
from app.domain.statement_parse import StatementParseError, parse_amount
from app.domain.tabular_statement import ExportRows, TransactionExport, resolve_encoding

Column = str | int


@dataclass(frozen=True)
class CsvProfile:
    date: Column
    description: Column
    date_format: str
    amount: Column | None = None
    debit: Column | None = None
    credit: Column | None = None
    balance: Column | None = None
    sign: Literal["as_printed", "inverted"] = "as_printed"
    header: Literal["auto", "present", "absent"] = "auto"
    delimiter: str = ","
    encoding: str = "utf-8"
    newest_first: bool = False
    # Sign of the printed running balance. Card exports often print the amount owed as positive:
    # "invert" turns that into Holdfast's sign (owed negative).
    balance_sign: Literal["as_is", "invert"] = "as_is"
    # What a negative number in a debit or credit column means. "reject" (default): an error,
    # because the column says which way the money moved. "reverse": the cell keeps its sign, so a
    # negative debit is a refund (inflow) and a negative credit a reversal (outflow).
    negative_split: Literal["reject", "reverse"] = "reject"


def csv_profile_from_mapping(data: Mapping[str, object]) -> CsvProfile:
    """Validate a decoded YAML mapping. Column indexes are zero-based."""

    def column(key: str, required: bool = False) -> Column | None:
        value = data.get(key)
        if value is None and not required:
            return None
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        raise StatementParseError(f"CSV profile {key} must be a column name or zero-based index")

    date_col, description = column("date", True), column("description", True)
    if date_col is None or description is None:
        raise StatementParseError("CSV profile requires date and description columns")
    amount, debit, credit, balance = (
        column(key) for key in ("amount", "debit", "credit", "balance")
    )
    if not (
        (amount is not None and debit is None and credit is None)
        or (amount is None and debit is not None and credit is not None)
    ):
        raise StatementParseError("CSV profile requires either amount or both debit and credit")
    columns = [c for c in (date_col, description, amount, debit, credit, balance) if c is not None]
    if len(set(columns)) != len(columns):
        raise StatementParseError("CSV profile columns must be distinct")

    date_format = data.get("date_format")
    if not isinstance(date_format, str) or not date_format.strip():
        raise StatementParseError("CSV profile requires date_format")
    if not {"y", "Y", "G"}.intersection(re.findall(r"%(.)", date_format)):
        raise StatementParseError("CSV profile date_format must specify a complete date")
    sample = datetime(2004, 11, 23)
    try:
        if datetime.strptime(sample.strftime(date_format), date_format).date() != sample.date():
            raise ValueError
    except ValueError:
        raise StatementParseError("CSV profile date_format must specify a complete date") from None
    sign = data.get("sign", "as_printed")
    if sign != "as_printed" and sign != "inverted":
        raise StatementParseError("CSV profile sign must be as_printed or inverted")
    if sign == "inverted" and amount is None:
        raise StatementParseError("CSV profile inverted sign requires an amount column")
    header = data.get("header", "auto")
    if header != "auto" and header != "present" and header != "absent":
        raise StatementParseError("CSV profile header must be auto, present or absent")
    if any(isinstance(c, str) for c in columns) and header == "absent":
        raise StatementParseError("CSV profile column names require a header")
    if all(isinstance(c, int) for c in columns) and header == "auto":
        header = "absent"  # Only explicitly matched column names can identify a header safely.
    delimiter = data.get("delimiter", ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\x00"':
        raise StatementParseError("CSV profile delimiter must be a single non-quote character")
    encoding = data.get("encoding", "utf-8")
    if not isinstance(encoding, str):
        raise StatementParseError("CSV profile encoding must be a text encoding")
    codec = resolve_encoding(encoding)
    if codec is None:
        raise StatementParseError(
            "CSV profile encoding must be a text encoding: utf-8, utf-16, utf-32, cp1252, "
            "iso-8859-1 or ascii"
        )
    balance_sign = data.get("balance_sign", "as_is")
    if balance_sign != "as_is" and balance_sign != "invert":
        raise StatementParseError("CSV profile balance_sign must be as_is or invert")
    if balance_sign == "invert" and balance is None:
        raise StatementParseError("CSV profile balance_sign requires a balance column")
    negative_split = data.get("negative_split", "reject")
    if negative_split != "reject" and negative_split != "reverse":
        raise StatementParseError("CSV profile negative_split must be reject or reverse")
    if negative_split == "reverse" and amount is not None:
        raise StatementParseError("CSV profile negative_split requires debit and credit columns")
    newest_first = data.get("newest_first", False)
    if not isinstance(newest_first, bool):
        raise StatementParseError("CSV profile newest_first must be a boolean")
    return CsvProfile(
        date_col,
        description,
        date_format,
        amount,
        debit,
        credit,
        balance,
        "inverted" if sign == "inverted" else "as_printed",
        "present" if header == "present" else "absent" if header == "absent" else "auto",
        delimiter,
        codec,
        newest_first,
        "invert" if balance_sign == "invert" else "as_is",
        "reverse" if negative_split == "reverse" else "reject",
    )


def _records(text: str, delimiter: str) -> Iterator[tuple[int, list[str]]]:
    reader = csv.reader(StringIO(text, newline=""), delimiter=delimiter, strict=True)
    while True:
        line = reader.line_num + 1
        try:
            row = next(reader)
        except StopIteration:
            return
        except csv.Error:
            raise StatementParseError(f"CSV row {line}: invalid CSV quoting or field") from None
        if row and (len(row) > 1 or row[0].strip()):
            yield line, row


def _row_amount(cells: Mapping[Column, str], profile: CsvProfile) -> Decimal:
    if profile.amount is not None:
        amount = parse_amount(cells[profile.amount])
        return amount.copy_negate() if profile.sign == "inverted" else amount
    elif profile.debit is not None and profile.credit is not None:
        debit_cell, credit_cell = cells[profile.debit], cells[profile.credit]
        if not (debit_cell or credit_cell):
            raise StatementParseError("missing debit and credit amounts")
        debit = parse_amount(debit_cell) if debit_cell else ZERO
        credit = parse_amount(credit_cell) if credit_cell else ZERO
        if debit and credit:
            raise StatementParseError("both debit and credit amounts are nonzero")
        if (debit < 0 or credit < 0) and profile.negative_split == "reject":
            raise StatementParseError("negative debit or credit amount")
        return credit - debit
    else:
        raise StatementParseError("CSV profile requires either amount or both debit and credit")


def parse_csv(source: str | bytes, profile: CsvProfile) -> TransactionExport:
    if isinstance(source, bytes):
        encoding = profile.encoding
        if source.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif source.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        elif source.startswith(codecs.BOM_UTF8):
            encoding = "utf-8-sig"
        try:
            source = source.decode(encoding)
        except (UnicodeError, LookupError):
            raise StatementParseError("CSV cannot be decoded using the profile encoding") from None

    records = _records(source.removeprefix("\ufeff"), profile.delimiter)
    columns = (
        profile.date,
        profile.description,
        profile.amount,
        profile.debit,
        profile.credit,
        profile.balance,
    )
    names = {column for column in columns if isinstance(column, str)}
    headings: list[str] = []
    if profile.header != "absent":
        for line, row in records:
            headings = [value.strip() for value in row]
            if names <= set(headings):
                if any(headings.count(name) != 1 for name in names):
                    raise StatementParseError(f"CSV row {line}: ambiguous header column")
                break
            if profile.header == "present" or names.intersection(headings):
                raise StatementParseError(f"CSV row {line}: missing header column")
        else:
            raise StatementParseError("CSV header not found")
    positions = {
        column: headings.index(column) if isinstance(column, str) else column
        for column in columns
        if column is not None
    }

    result = ExportRows()
    for line, row in records:
        try:
            cells = {column: row[position].strip() for column, position in positions.items()}
            posted = datetime.strptime(cells[profile.date], profile.date_format).date()
            description = cells[profile.description]
            if not description:
                raise StatementParseError("missing description")
            amount = _row_amount(cells, profile)
            balance = parse_amount(cells[profile.balance]) if profile.balance is not None else None
            if balance is not None and profile.balance_sign == "invert":
                balance = -balance
            result.add(posted, description, amount, balance)
        except StatementParseError as exc:
            raise StatementParseError(f"CSV row {line}: {exc}") from None
        except IndexError:
            raise StatementParseError(f"CSV row {line}: missing column") from None
        except ValueError:
            raise StatementParseError(f"CSV row {line}: unreadable date") from None
        except DecimalException:
            raise StatementParseError(f"CSV row {line}: amount out of range") from None

    closing = result.balances[0 if profile.newest_first else -1] if result.balances else None
    opening, reconciliation = result.reconcile(
        closing,
        newest_first=profile.newest_first,
    )
    return TransactionExport(tuple(result.txns), reconciliation, opening, closing)
