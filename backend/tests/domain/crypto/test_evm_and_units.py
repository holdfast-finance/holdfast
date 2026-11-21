"""EIP-55 vectors (from the EIP), the chain registry, and exact base-unit conversion."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.crypto.chains import CHAINS, EVM_CHAINS, ChainKind, get_chain
from app.domain.crypto.errors import CryptoInputError
from app.domain.crypto.evm import address_key, keccak256, normalise_evm_address, to_checksum_address
from app.domain.crypto.units import fits_quantity, from_base_units, to_base_units

# EIP-55 examples: all caps, all lower, and mixed-case checksummed.
EIP55 = [
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]
MIXED = EIP55[4:]


def test_keccak_is_not_nist_sha3() -> None:
    assert (
        keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


@pytest.mark.parametrize("address", MIXED)
def test_eip55_vectors_checksum_to_themselves(address: str) -> None:
    assert to_checksum_address(address.lower()) == address
    assert normalise_evm_address(address) == address


@pytest.mark.parametrize("address", EIP55[:4])
def test_unchecksummed_addresses_are_accepted_and_normalised(address: str) -> None:
    out = normalise_evm_address(address)
    assert out.lower() == address.lower() and out == to_checksum_address(address)


@pytest.mark.parametrize("address", MIXED)
def test_a_wrong_case_is_refused(address: str) -> None:
    index = next(i for i, c in enumerate(address) if i > 1 and c.isalpha())
    flipped = address[:index] + address[index].swapcase() + address[index + 1 :]
    with pytest.raises(CryptoInputError, match="checksum"):
        normalise_evm_address(flipped)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",  # no 0x
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe",  # 39 digits
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAedd",
        "0xZZAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "vitalik.eth",
        "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    ],
)
def test_malformed_evm_addresses_are_refused(bad: str) -> None:
    with pytest.raises(CryptoInputError):
        normalise_evm_address(bad)


def test_a_raw_private_key_is_refused_without_echo() -> None:
    shaped_like_a_key = "0x" + "ab" * 32
    with pytest.raises(CryptoInputError) as err:
        normalise_evm_address(shaped_like_a_key)
    assert "abab" not in str(err.value)


def test_address_key_is_case_insensitive() -> None:
    assert address_key(MIXED[0]) == address_key(MIXED[0].lower()) == MIXED[0].lower()


def test_registry_has_the_five_evm_chains_and_bitcoin_as_data() -> None:
    assert {c.slug for c in EVM_CHAINS} == {"ethereum", "base", "arbitrum", "optimism", "polygon"}
    assert [c.chain_id for c in EVM_CHAINS] == [1, 8453, 42161, 10, 137]
    assert get_chain("bitcoin").kind is ChainKind.BITCOIN
    assert get_chain("bitcoin").native_decimals == 8
    assert all(c.native_decimals == 18 for c in EVM_CHAINS)
    assert len({c.slug for c in CHAINS}) == len(CHAINS)
    with pytest.raises(CryptoInputError):
        get_chain("solana")


# ------------------------------------------------------------------ units


def test_wei_conversion_is_exact_past_28_digits() -> None:
    raw = 123_456_789_012_345_678_901_234_567_890
    assert from_base_units(raw, 18) == Decimal("123456789012.345678901234567890")
    assert from_base_units(1, 18) == Decimal("0.000000000000000001")
    assert from_base_units(-150_000_000, 8) == Decimal("-1.50000000")


@given(st.integers(min_value=-(10**38), max_value=10**38), st.integers(min_value=0, max_value=18))
def test_base_unit_round_trip(raw: int, decimals: int) -> None:
    assert to_base_units(from_base_units(raw, decimals), decimals) == raw


def test_a_fraction_of_a_base_unit_is_refused() -> None:
    with pytest.raises(CryptoInputError):
        to_base_units(Decimal("0.123456789"), 8)
    assert to_base_units(Decimal("0.12345678"), 8) == 12_345_678


def test_decimals_beyond_the_column_are_refused() -> None:
    with pytest.raises(CryptoInputError):
        from_base_units(1, 19)


@pytest.mark.parametrize(
    ("value", "fits"),
    [
        (Decimal("0.000000000000000001"), True),
        (Decimal("99999999999999999999.999999999999999999"), True),
        (Decimal("100000000000000000000"), False),
        (Decimal("0.0000000000000000001"), False),
        (Decimal("1.2300000000000000000"), True),  # trailing zeros are not precision
        (Decimal("1E+19"), True),
        (Decimal("1E+20"), False),
        (Decimal("1E-25"), False),
        (Decimal("0E-25"), True),
        (Decimal("NaN"), False),
        (Decimal("Infinity"), False),
    ],
)
def test_fits_quantity_matches_numeric_38_18(value: Decimal, fits: bool) -> None:
    assert fits_quantity(value) is fits
