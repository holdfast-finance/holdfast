"""Bitcoin watch-only keys: parse, canonicalise and derive addresses (ADR 0020).

Accepts public data only: an extended public key (xpub/ypub/zpub and the testnet tpub/upub/vpub
families, optionally with a ``[fingerprint/path]`` origin) or an output descriptor (``wpkh``,
``sh(wpkh)``, ``pkh``, ``tr`` with a key path only, ``wsh(sortedmulti)``). Anything that looks
like a private key or seed phrase is refused by the guard before parsing, with a fixed error.

The only elliptic-curve and script code is the ``embit`` library's, confined to this module. We keep
our own value types so replacing it is a one-module change.

A key is normalised to **one canonical descriptor** with a ``/<0;1>/*`` branch (receive and change)
and an ``xpub``/``tpub`` serialisation, plus its BIP380 checksum. A descriptor that names only one
branch (``/0/*``) is widened to both: a watch-only wallet must see its change to reconcile.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from embit import base58, bip32
from embit.descriptor import Descriptor
from embit.descriptor.checksum import add_checksum
from embit.descriptor.errors import DescriptorError
from embit.networks import NETWORKS
from embit.script import address_to_scriptpubkey

from app.domain.crypto.errors import CryptoInputError
from app.domain.crypto.guard import reject_secret_material

MAX_DERIVE_COUNT = 1000
MAX_KEYS = 15


class Network(StrEnum):
    MAINNET = "mainnet"
    TESTNET = "testnet"
    SIGNET = "signet"
    REGTEST = "regtest"


class ScriptType(StrEnum):
    P2PKH = "p2pkh"
    P2SH_P2WPKH = "p2sh_p2wpkh"
    P2WPKH = "p2wpkh"
    P2TR = "p2tr"
    P2WSH = "p2wsh"  # wsh(sortedmulti(...))


_EMBIT_NET = {
    Network.MAINNET: "main",
    Network.TESTNET: "test",
    Network.SIGNET: "signet",
    Network.REGTEST: "regtest",
}

# Extended public key version bytes (SLIP-132). Value: (network family, implied script or None).
_MAIN, _TEST = "main", "test"
_VERSIONS: dict[bytes, tuple[str, ScriptType | None]] = {
    bytes.fromhex("0488b21e"): (_MAIN, None),  # xpub
    bytes.fromhex("049d7cb2"): (_MAIN, ScriptType.P2SH_P2WPKH),  # ypub
    bytes.fromhex("04b24746"): (_MAIN, ScriptType.P2WPKH),  # zpub
    bytes.fromhex("043587cf"): (_TEST, None),  # tpub
    bytes.fromhex("044a5262"): (_TEST, ScriptType.P2SH_P2WPKH),  # upub
    bytes.fromhex("045f1cf6"): (_TEST, ScriptType.P2WPKH),  # vpub
}
# Multisig-specific serialisations (Ypub, Zpub, Upub, Vpub): the script lives in a descriptor.
_MULTISIG_VERSIONS = {bytes.fromhex(h) for h in ("0295b43f", "02aa7ed3", "024289ef", "02575483")}
_CANONICAL_VERSION = {_MAIN: bytes.fromhex("0488b21e"), _TEST: bytes.fromhex("043587cf")}

_KEY = re.compile(
    r"(?:\[(?P<fp>[0-9a-fA-F]{8})(?P<opath>(?:/[0-9]{1,10}['hH]?)*)\])?"
    r"(?P<xkey>[1-9A-HJ-NP-Za-km-z]{100,115})"
    r"(?P<branch>/(?:0|1|<0;1>|<1;0>|\{0,1\}|\{1,0\})/\*|/\*)?"
)
_WRAPPERS = (
    ("sh(wpkh(", "))", ScriptType.P2SH_P2WPKH),
    ("wpkh(", ")", ScriptType.P2WPKH),
    ("pkh(", ")", ScriptType.P2PKH),
    ("tr(", ")", ScriptType.P2TR),
)
_CHECKSUM = re.compile(r"#[a-z0-9]{8}$")


@dataclass(frozen=True)
class WatchKey:
    """A validated, canonical, public-only Bitcoin wallet key."""

    descriptor: str  # canonical, with checksum. Public data, but encrypt it at rest.
    script_type: ScriptType
    network: Network
    fingerprint: (
        str | None
    )  # master key fingerprint (first key for multisig), if the origin gave it
    derivation_path: str | None  # ``m/84h/0h/0h``, if the origin gave it
    threshold: int | None = None  # multisig m
    key_count: int = 1

    @property
    def identity_hash(self) -> str:
        """Stable per-wallet id: SHA-256 of the canonical descriptor without its ``[origin]``
        and checksum. The same key with or without origin metadata is the same wallet."""
        bare = re.sub(r"\[[^\]]*\]", "", self.descriptor.rsplit("#", 1)[0])
        return hashlib.sha256(bare.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class DerivedAddress:
    address: str
    index: int
    is_change: bool
    path: str  # full path when the origin is known, else ``<branch>/<index>``


def _fail(message: str) -> CryptoInputError:
    return CryptoInputError(message)


def _normalise_path(raw: str) -> str:
    return "m" + raw.replace("'", "h").replace("H", "h")


def _decode_key(
    xkey: str, family_hint: str | None
) -> tuple[bip32.HDKey, str, ScriptType | None, bool]:
    """Return (public key as xpub-serialised HDKey, family, implied script, is_multisig_version)."""
    try:
        raw = base58.decode_check(xkey)
    except Exception:
        raise _fail("That is not a valid extended public key (checksum or format).") from None
    if len(raw) != 78:
        raise _fail("That is not a valid extended public key (checksum or format).")
    version = bytes(raw[:4])
    multisig = version in _MULTISIG_VERSIONS
    if multisig:
        family = (
            _MAIN if version in (bytes.fromhex("0295b43f"), bytes.fromhex("02aa7ed3")) else _TEST
        )
        implied: ScriptType | None = None
    elif version in _VERSIONS:
        family, implied = _VERSIONS[version]
    else:
        raise _fail("That is not a supported extended public key.")
    swapped = _CANONICAL_VERSION[family] + bytes(raw[4:])
    try:
        key = bip32.HDKey.from_base58(base58.encode_check(swapped))
    except Exception:
        raise _fail("That is not a valid extended public key (checksum or format).") from None
    if key.is_private:  # unreachable behind the guard; belt and braces
        raise _fail("That is not a public key.")
    return key, family, implied, multisig


def _resolve_network(family: str, requested: Network | None) -> Network:
    if family == _MAIN:
        if requested not in (None, Network.MAINNET):
            raise _fail("That key is for Bitcoin mainnet, but another network was chosen.")
        return Network.MAINNET
    if requested in (None, Network.TESTNET):
        return Network.TESTNET
    if requested in (Network.SIGNET, Network.REGTEST):
        return requested
    raise _fail("That key is for a test network, but mainnet was chosen.")


@dataclass(frozen=True)
class _ParsedKey:
    expr: str  # canonical key expression (origin + xpub + /<0;1>/*)
    family: str
    implied: ScriptType | None
    multisig_version: bool
    fingerprint: str | None
    path: str | None


def _parse_key_expr(expr: str) -> _ParsedKey:
    m = _KEY.fullmatch(expr)
    if m is None:
        raise _fail("That key is not a supported extended public key expression.")
    key, family, implied, multisig = _decode_key(m["xkey"], None)
    fp = m["fp"].lower() if m["fp"] else None
    path = _normalise_path(m["opath"]) if m["fp"] is not None else None
    if path == "m":
        path = None
    origin = f"[{fp}{path[1:] if path else ''}]" if fp else ""
    xpub = key.to_base58()
    return _ParsedKey(f"{origin}{xpub}/<0;1>/*", family, implied, multisig, fp, path)


def _split_checksum(text: str) -> tuple[str, str | None]:
    m = _CHECKSUM.search(text)
    if m is None:
        if "#" in text:
            raise _fail("That descriptor checksum is malformed.")
        return text, None
    return text[: m.start()], m.group(0)[1:]


def _split_args(inner: str) -> list[str]:
    """Split on top-level commas (``<0;1>`` and ``{0,1}`` keep theirs)."""
    parts: list[str] = []
    depth = 0
    cur = ""
    for ch in inner:
        if ch in "[{<":
            depth += 1
        elif ch in "]}>":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def parse_watch_key(
    text: str, *, script_type: ScriptType | None = None, network: Network | None = None
) -> WatchKey:
    """Validate an extended public key or descriptor and return its canonical form.

    ``script_type`` is needed only for a bare ``xpub``/``tpub`` (which does not say which script it
    is for). The guard runs first: secret material raises ``SecretMaterialError`` with a fixed
    message.
    """
    reject_secret_material(text)
    body, supplied = _split_checksum(text.strip())
    if not body:
        raise _fail("Enter an extended public key or an output descriptor.")
    if supplied is not None:
        try:
            expected = add_checksum(body).rsplit("#", 1)[1]
        except DescriptorError:
            raise _fail("That descriptor could not be read.") from None
        if expected != supplied:
            raise _fail("That descriptor's checksum does not match. Check it was copied in full.")

    for prefix, suffix, wrapped in _WRAPPERS:
        if body.startswith(prefix) and body.endswith(suffix):
            parsed = [_parse_key_expr(body[len(prefix) : len(body) - len(suffix)])]
            return _build(parsed, wrapped, script_type, network, None)
    if body.startswith("wsh(sortedmulti(") and body.endswith("))"):
        args = _split_args(body[len("wsh(sortedmulti(") : -2])
        if len(args) < 3 or not args[0].isdigit():
            raise _fail("That multisig descriptor is not supported.")
        threshold, keys = int(args[0]), args[1:]
        if not 1 <= threshold <= len(keys) <= MAX_KEYS:
            raise _fail("That multisig descriptor is not supported.")
        return _build(
            [_parse_key_expr(k) for k in keys], ScriptType.P2WSH, script_type, network, threshold
        )
    if re.match(r"^[a-z]+\(", body):
        raise _fail(
            "Only wpkh, sh(wpkh), pkh, tr (key path) and wsh(sortedmulti) descriptors "
            "are supported."
        )
    return _build([_parse_key_expr(body)], None, script_type, network, None)


def _build(
    keys: list[_ParsedKey],
    wrapped: ScriptType | None,
    requested_script: ScriptType | None,
    requested_network: Network | None,
    threshold: int | None,
) -> WatchKey:
    families = {k.family for k in keys}
    if len(families) != 1:
        raise _fail("All keys in a descriptor must be for the same network.")
    network = _resolve_network(families.pop(), requested_network)

    if wrapped is ScriptType.P2WSH:
        script = ScriptType.P2WSH
    else:
        key = keys[0]
        if key.multisig_version:
            raise _fail("Multisig keys must be given inside a wsh(sortedmulti(...)) descriptor.")
        if wrapped is not None:
            script = wrapped
            if key.implied is not None and key.implied is not wrapped:
                raise _fail("The key type does not match the descriptor's script type.")
        else:  # a bare key: the version bytes or the caller say which script
            if key.implied is not None:
                script = key.implied
                if requested_script not in (None, script):
                    raise _fail("The key type does not match the chosen script type.")
            elif requested_script in (None, ScriptType.P2WSH):
                raise _fail(
                    "This key does not say which address type it is for. Choose one: "
                    "p2wpkh, p2sh_p2wpkh, p2tr or p2pkh."
                )
            else:
                script = requested_script
    if wrapped is not None and requested_script not in (None, script):
        raise _fail("The chosen script type does not match the descriptor.")

    exprs = sorted(k.expr for k in keys)
    descriptor = _wrap(script, exprs, threshold)
    canonical = add_checksum(descriptor)
    first = keys[0]
    return WatchKey(
        descriptor=canonical,
        script_type=script,
        network=network,
        fingerprint=first.fingerprint,
        derivation_path=first.path,
        threshold=threshold,
        key_count=len(keys),
    )


def _wrap(script: ScriptType, exprs: list[str], threshold: int | None) -> str:
    if script is ScriptType.P2WSH:
        return f"wsh(sortedmulti({threshold},{','.join(exprs)}))"
    inner = exprs[0]
    return {
        ScriptType.P2WPKH: f"wpkh({inner})",
        ScriptType.P2SH_P2WPKH: f"sh(wpkh({inner}))",
        ScriptType.P2PKH: f"pkh({inner})",
        ScriptType.P2TR: f"tr({inner})",
    }[script]


def derive_addresses(
    key: WatchKey, *, change: bool, start: int, count: int
) -> tuple[DerivedAddress, ...]:
    """Derive ``count`` addresses of the receive (``change=False``) or change branch from
    ``start``."""
    if start < 0 or not 0 <= count <= MAX_DERIVE_COUNT:
        raise _fail("That address range is not allowed.")
    descriptor = Descriptor.from_string(key.descriptor.rsplit("#", 1)[0])
    net = NETWORKS[_EMBIT_NET[key.network]]
    branch = 1 if change else 0
    base = (key.derivation_path + "/") if key.derivation_path and key.threshold is None else ""
    out: list[DerivedAddress] = []
    for index in range(start, start + count):
        address = descriptor.derive(index, branch_index=branch).script_pubkey().address(net)
        out.append(DerivedAddress(address, index, change, f"{base}{branch}/{index}"))
    return tuple(out)


def addresses_needed(last_used_index: int | None, gap_limit: int) -> int:
    """How many addresses of one branch to watch: everything up to the last used one, plus the
    gap."""
    if gap_limit < 1:
        raise _fail("The gap limit must be at least 1.")
    return (0 if last_used_index is None else last_used_index + 1) + gap_limit


def normalise_bitcoin_address(text: str, network: Network = Network.MAINNET) -> str:
    """Validate a single Bitcoin address for ``network`` and return its canonical spelling.

    Bech32 and bech32m are lower-cased; base58 keeps its case (it is case-sensitive).
    """
    reject_secret_material(text)
    candidate = text.strip()
    if not candidate or " " in candidate:
        raise _fail("That is not a valid Bitcoin address.")
    net = NETWORKS[_EMBIT_NET[network]]
    spelled = candidate
    if candidate.lower().startswith(net["bech32"] + "1"):
        if candidate not in (candidate.lower(), candidate.upper()):
            raise _fail("That is not a valid Bitcoin address.")
        spelled = candidate.lower()
    try:
        script = address_to_scriptpubkey(spelled)
        roundtrip = str(script.address(net))
    except Exception:
        raise _fail("That is not a valid Bitcoin address.") from None
    if roundtrip != spelled:
        raise _fail("That is not a valid Bitcoin address for this network.")
    return roundtrip


__all__ = [
    "DerivedAddress",
    "Network",
    "ScriptType",
    "WatchKey",
    "addresses_needed",
    "derive_addresses",
    "normalise_bitcoin_address",
    "parse_watch_key",
]
