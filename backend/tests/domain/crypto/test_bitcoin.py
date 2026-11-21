"""Bitcoin watch-only parsing and derivation against published test vectors.

Vectors: BIP49, BIP84 and BIP86 (the standard test mnemonic's public keys and addresses, public
data). Nothing private is used or derived.
"""

from __future__ import annotations

import hashlib

import pytest
from embit import base58, bech32, bip32
from embit.descriptor.checksum import add_checksum

from app.domain.crypto.bitcoin import (
    Network,
    ScriptType,
    addresses_needed,
    derive_addresses,
    normalise_bitcoin_address,
    parse_watch_key,
)
from app.domain.crypto.errors import CryptoInputError

ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"  # noqa: E501
YPUB = "ypub6Ww3ibxVfGzLrAH1PNcjyAWenMTbbAosGNB6VvmSEgytSER9azLDWCxoJwW7Ke7icmizBMXrzBx9979FfaHxHcrArf3zbeJJJUZPf663zsP"  # noqa: E501
XPUB_86 = "xpub6BgBgsespWvERF3LHQu6CnqdvfEvtMcQjYrcRzx53QJjSxarj2afYWcLteoGVky7D3UKDP9QyrLprQ3VCECoY49yfdDEHGCtMMj92pReUsQ"  # noqa: E501
FP = "73c5da0a"

BIP84_RECEIVE = [
    "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
    "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g",
]
BIP84_CHANGE = ["bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"]
BIP49_FIRST = "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"
BIP86_RECEIVE = [
    "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
    "bc1p4qhjn9zdvkux4e44uhx8tc55attvtyu358kutcqkudyccelu0was9fqzwh",
]
BIP86_CHANGE = ["bc1p3qkhfews2uk44qtvauqyr2ttdsw7svhkl9nkm9s9c3x4ax5h60wqwruhk7"]


def addrs(key_text: str, *, change: bool = False, count: int = 2, **kw: object) -> list[str]:
    key = parse_watch_key(key_text, **kw)  # type: ignore[arg-type]
    return [a.address for a in derive_addresses(key, change=change, start=0, count=count)]


# ------------------------------------------------------------------ published vectors


def test_bip84_zpub_receive_and_change() -> None:
    assert addrs(ZPUB) == BIP84_RECEIVE
    assert addrs(ZPUB, change=True, count=1) == BIP84_CHANGE


def test_bip49_ypub_first_address() -> None:
    assert addrs(YPUB, count=1) == [BIP49_FIRST]


def test_bip86_taproot_from_xpub_needs_the_script_type() -> None:
    with pytest.raises(CryptoInputError, match="which address type"):
        parse_watch_key(XPUB_86)
    assert addrs(XPUB_86, script_type=ScriptType.P2TR) == BIP86_RECEIVE
    assert addrs(XPUB_86, script_type=ScriptType.P2TR, change=True, count=1) == BIP86_CHANGE


def test_descriptors_give_the_same_addresses() -> None:
    assert addrs(f"wpkh([{FP}/84h/0h/0h]{ZPUB}/0/*)") == BIP84_RECEIVE
    assert addrs(f"tr([{FP}/86h/0h/0h]{XPUB_86}/<0;1>/*)", count=2) == BIP86_RECEIVE
    assert addrs(f"sh(wpkh({YPUB}/0/*))", count=1) == [BIP49_FIRST]


# ------------------------------------------------------------------ canonical form


def test_canonical_descriptor_is_xpub_serialised_with_both_branches_and_a_checksum() -> None:
    key = parse_watch_key(f"[{FP}/84'/0'/0']{ZPUB}")
    assert key.script_type is ScriptType.P2WPKH
    assert key.network is Network.MAINNET
    assert key.fingerprint == FP
    assert key.derivation_path == "m/84h/0h/0h"
    assert key.descriptor.startswith(f"wpkh([{FP}/84h/0h/0h]xpub")
    assert "/<0;1>/*)#" in key.descriptor
    body, checksum = key.descriptor.rsplit("#", 1)
    assert add_checksum(body) == key.descriptor and len(checksum) == 8
    assert "zpub" not in key.descriptor


def test_equivalent_inputs_share_an_identity() -> None:
    a = parse_watch_key(f"[{FP}/84h/0h/0h]{ZPUB}")
    b = parse_watch_key(f"wpkh([{FP.upper()}/84'/0'/0']{ZPUB}/0/*)")
    c = parse_watch_key(f"wpkh([{FP}/84h/0h/0h]{ZPUB}/{{0,1}}/*)")
    assert a.identity_hash == b.identity_hash == c.identity_hash
    assert a.identity_hash != parse_watch_key(YPUB).identity_hash


def test_derived_path_uses_the_origin_when_known() -> None:
    key = parse_watch_key(f"[{FP}/84h/0h/0h]{ZPUB}")
    first = derive_addresses(key, change=False, start=0, count=1)[0]
    assert (first.path, first.index, first.is_change) == ("m/84h/0h/0h/0/0", 0, False)
    change = derive_addresses(key, change=True, start=3, count=1)[0]
    assert (change.path, change.is_change) == ("m/84h/0h/0h/1/3", True)
    assert derive_addresses(parse_watch_key(ZPUB), change=False, start=0, count=1)[0].path == "0/0"


def test_descriptor_checksum_is_verified_when_given() -> None:
    body = f"wpkh([{FP}/84h/0h/0h]{ZPUB}/0/*)"
    assert addrs(add_checksum(body), count=1) == BIP84_RECEIVE[:1]
    wrong = body + "#aaaaaaaa"
    with pytest.raises(CryptoInputError, match="checksum"):
        parse_watch_key(wrong)
    with pytest.raises(CryptoInputError, match="checksum"):
        parse_watch_key(body + "#short")


# ------------------------------------------------------------------ rejections


def test_a_corrupted_key_is_refused_without_echo() -> None:
    bad = ZPUB[:-1] + ("A" if ZPUB[-1] != "A" else "B")
    with pytest.raises(CryptoInputError) as err:
        parse_watch_key(bad)
    assert bad not in str(err.value) and ZPUB[:20] not in str(err.value)


@pytest.mark.parametrize(
    "text",
    [
        f"tr({XPUB_86}/0/*,{{pk({XPUB_86})}})",  # script tree: key path only
        f"wsh(multi(2,{XPUB_86},{ZPUB}))",  # unsorted multisig
        f"sh(wsh(sortedmulti(2,{XPUB_86},{ZPUB})))",
        "raw(deadbeef)",
        f"wpkh({XPUB_86}/5/7)",  # a fixed child, not a wallet
        f"wpkh({XPUB_86}/0/*h)",  # hardened wildcard cannot be watched from an xpub
        "not a key",
        "",
        "   ",
    ],
)
def test_unsupported_or_malformed_input_is_refused(text: str) -> None:
    with pytest.raises(CryptoInputError):
        parse_watch_key(text, script_type=ScriptType.P2WPKH)


def test_key_type_must_match_the_script() -> None:
    with pytest.raises(CryptoInputError, match="does not match"):
        parse_watch_key(f"wpkh({YPUB}/0/*)")  # ypub is for sh(wpkh)
    with pytest.raises(CryptoInputError, match="does not match"):
        parse_watch_key(ZPUB, script_type=ScriptType.P2TR)


def test_range_limits() -> None:
    key = parse_watch_key(ZPUB)
    with pytest.raises(CryptoInputError):
        derive_addresses(key, change=False, start=-1, count=1)
    with pytest.raises(CryptoInputError):
        derive_addresses(key, change=False, start=0, count=1001)
    assert derive_addresses(key, change=False, start=0, count=0) == ()


def test_gap_limit_window() -> None:
    assert addresses_needed(None, 20) == 20
    assert addresses_needed(0, 20) == 21
    assert addresses_needed(4, 20) == 25
    with pytest.raises(CryptoInputError):
        addresses_needed(None, 0)


# ------------------------------------------------------------------ test networks


def _as_tpub(xpub: str) -> str:
    raw = bytes(base58.decode_check(xpub))
    return base58.encode_check(bytes.fromhex("043587cf") + raw[4:])


def test_testnet_key_uses_test_addresses_and_refuses_mainnet() -> None:
    tpub = _as_tpub(XPUB_86)
    key = parse_watch_key(tpub, script_type=ScriptType.P2TR)
    assert key.network is Network.TESTNET
    assert derive_addresses(key, change=False, start=0, count=1)[0].address.startswith("tb1p")
    signet = parse_watch_key(tpub, script_type=ScriptType.P2TR, network=Network.SIGNET)
    assert signet.network is Network.SIGNET
    with pytest.raises(CryptoInputError, match="test network"):
        parse_watch_key(tpub, script_type=ScriptType.P2TR, network=Network.MAINNET)
    with pytest.raises(CryptoInputError, match="mainnet"):
        parse_watch_key(ZPUB, network=Network.TESTNET)


# ------------------------------------------------------------------ multisig


def _pubkey(xpub: str, branch: int, index: int) -> bytes:
    return bytes(bip32.HDKey.from_base58(xpub).derive([branch, index]).sec())


def test_wsh_sortedmulti_matches_an_independent_script_build() -> None:
    """Build the 2-of-2 witness script by hand (sorted keys) and compare the address."""
    text = f"wsh(sortedmulti(2,[{FP}/48h/0h/0h/2h]{ZPUB}/0/*,{XPUB_86}/0/*))"
    swapped = f"wsh(sortedmulti(2,{XPUB_86}/0/*,[{FP}/48h/0h/0h/2h]{ZPUB}/0/*))"
    key = parse_watch_key(text)
    assert (key.script_type, key.threshold, key.key_count) == (ScriptType.P2WSH, 2, 2)
    assert key.identity_hash == parse_watch_key(swapped).identity_hash
    for index in range(3):
        keys = sorted([_pubkey(ZPUB, 0, index), _pubkey(XPUB_86, 0, index)])
        script = b"\x52" + b"".join(b"\x21" + k for k in keys) + b"\x52\xae"
        expected = bech32.encode("bc", 0, hashlib.sha256(script).digest())
        got = derive_addresses(key, change=False, start=index, count=1)[0].address
        assert got == expected


@pytest.mark.parametrize("threshold", [0, 3])
def test_multisig_threshold_must_fit(threshold: int) -> None:
    with pytest.raises(CryptoInputError):
        parse_watch_key(f"wsh(sortedmulti({threshold},{XPUB_86}/0/*,{ZPUB}/0/*))")


# ------------------------------------------------------------------ single addresses


@pytest.mark.parametrize(
    ("given", "canonical"),
    [
        (BIP84_RECEIVE[0], BIP84_RECEIVE[0]),
        (BIP84_RECEIVE[0].upper(), BIP84_RECEIVE[0]),
        (f"  {BIP49_FIRST}\n", BIP49_FIRST),
        (BIP86_RECEIVE[0], BIP86_RECEIVE[0]),
    ],
)
def test_bitcoin_address_normalisation(given: str, canonical: str) -> None:
    assert normalise_bitcoin_address(given) == canonical


@pytest.mark.parametrize(
    "bad",
    [
        "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyx",  # bad checksum
        "Bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",  # mixed case
        "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",  # testnet address on mainnet
        "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgg",
        "hello world",
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",  # an EVM address is not Bitcoin
        "",
    ],
)
def test_bad_bitcoin_addresses_are_refused(bad: str) -> None:
    with pytest.raises(CryptoInputError):
        normalise_bitcoin_address(bad)


def test_testnet_address_accepted_on_testnet() -> None:
    addr = "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx"
    assert normalise_bitcoin_address(addr, Network.TESTNET) == addr
