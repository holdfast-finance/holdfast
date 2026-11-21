"""The secret-material guard (hard rule 11). No real key or seed appears here.

Every "secret" is shaped like one but is not a real key: a prefix plus filler characters, or words
picked by index from the public BIP39 list. The point is the refusal and that the error never echoes
the input.
"""

from __future__ import annotations

import pytest
from embit.wordlists.bip39 import WORDLIST

from app.domain.crypto.bitcoin import normalise_bitcoin_address, parse_watch_key
from app.domain.crypto.errors import CryptoInputError, SecretMaterialError
from app.domain.crypto.guard import contains_secret_material, reject_secret_material

BASE58_FILLER = "A" * 107


def words(n: int) -> str:
    return " ".join(WORDLIST[(i * 173 + 11) % 2048] for i in range(n))


SECRETS = {
    "xprv": "xprv" + BASE58_FILLER,
    "yprv": "yprv" + BASE58_FILLER,
    "zprv": "zprv" + BASE58_FILLER,
    "Zprv": "Zprv" + BASE58_FILLER,
    "tprv": "tprv" + BASE58_FILLER,
    "vprv": "vprv" + BASE58_FILLER,
    "xprv in a descriptor": "wpkh([73c5da0a/84h/0h/0h]xprv" + BASE58_FILLER + "/0/*)",
    "xprv with origin and branch": "[00000000/84h]xprv" + BASE58_FILLER + "/<0;1>/*",
    "wif uncompressed": "5" + "H" * 50,
    "wif compressed": "K" + "w" * 51,
    "wif testnet": "c" + "T" * 51,
    "hex key": "ab" * 32,
    "hex key with 0x": "0x" + "cd" * 32,
    "12 words": words(12),
    "24 words": words(24),
    "12 words, commas": ", ".join(words(12).split()),
    "12 words, newlines": "\n".join(words(12).split()),
    "partial list": words(8),
    "twelve plain words, any language": " ".join(["lorem", "ipsum", "dolor", "sit", "amet"] * 3),
}


@pytest.mark.parametrize("secret", SECRETS.values(), ids=SECRETS.keys())
def test_guard_rejects_secret_looking_input(secret: str) -> None:
    assert contains_secret_material(secret)
    with pytest.raises(SecretMaterialError):
        reject_secret_material(secret)


@pytest.mark.parametrize("secret", SECRETS.values(), ids=SECRETS.keys())
def test_error_never_echoes_the_input(secret: str) -> None:
    for call in (
        lambda: reject_secret_material(secret),
        lambda: parse_watch_key(secret),
        lambda: normalise_bitcoin_address(secret),
    ):
        with pytest.raises(CryptoInputError) as err:
            call()
        shown = f"{err.value!s} {err.value!r} {err.value.args!r} {err.value.message}"
        assert err.value.__cause__ is None
        assert err.value.__suppress_context__ or err.value.__context__ is None
        for chunk in [*secret.replace(",", " ").split(), secret]:
            assert chunk not in shown


def test_the_message_is_fixed() -> None:
    messages = set()
    for secret in SECRETS.values():
        with pytest.raises(SecretMaterialError) as err:
            reject_secret_material(secret)
        messages.add(str(err.value))
    assert messages == {SecretMaterialError.MESSAGE}


@pytest.mark.parametrize(
    "public",
    [
        "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu",
        "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf",
        "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs",
        "[73c5da0a/84h/0h/0h]zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs/<0;1>/*",
        "Cold storage, savings for the house",
        "",
    ],
)
def test_public_data_passes(public: str) -> None:
    assert not contains_secret_material(public)
    reject_secret_material(public)


def test_oversized_input_is_refused_without_echo() -> None:
    blob = "q" * 5000
    with pytest.raises(CryptoInputError) as err:
        reject_secret_material(blob)
    assert blob not in str(err.value)
