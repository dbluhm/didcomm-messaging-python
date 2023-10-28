"""Askar backend for DIDComm Messaging."""
from collections import OrderedDict
import json
from typing import Mapping, Optional, Union
from didcomm_messaging.crypto import KMS, SecretsManager
from didcomm_messaging.jwe import (
    JweBuilder,
    JweEnvelope,
    JweRecipient,
    b64url,
)
from didcomm_messaging.kms import CryptoService, CryptoServiceError, PublicKey, SecretKey
from didcomm_messaging.multiformats import multibase, multicodec

try:
    from aries_askar import Key, ecdh, AskarError, KeyAlg, Store
except ImportError:
    raise ImportError("Askar backend requires the 'askar' extra to be installed")


class AskarKey(PublicKey):
    """Public key implementation for Askar."""

    codec_to_alg = {
        "ed25519-pub": KeyAlg.ED25519,
        "x25519-pub": KeyAlg.X25519,
        "secp256k1-pub": KeyAlg.K256,
    }
    alg_to_codec = {v: k for k, v in codec_to_alg.items()}

    type_to_alg = {
        "Ed25519VerificationKey2018": KeyAlg.ED25519,
        "X25519KeyAgreementKey2019": KeyAlg.X25519,
        "Ed25519VerificationKey2020": KeyAlg.ED25519,
        "X25519KeyAgreementKey2020": KeyAlg.X25519,
        "EcdsaSecp256k1VerificationKey2019": KeyAlg.K256,
    }

    def __init__(self, key: Key, kid: str):
        """Initialize a new AskarKey instance."""
        self.key = key
        self._kid = kid

        codec = self.alg_to_codec.get(self.key.algorithm)
        if not codec:
            raise ValueError("Unsupported key type")

        self._multikey = multibase.encode(
            multicodec.wrap(multicodec.multicodec(codec), self.key.get_public_bytes()),
            "base58btc",
        )

    @classmethod
    def _multikey_to_key(cls, multikey: str) -> Key:
        """Convert a multibase-encoded key to an Askar Key instance."""
        decoded = multibase.decode(multikey)
        codec, key = multicodec.unwrap(decoded)
        alg = cls.codec_to_alg.get(codec.name)
        if not alg:
            raise ValueError("Unsupported key type: {codec.name}")
        try:
            return Key.from_public_bytes(alg, key)
        except AskarError as err:
            raise ValueError("Invalid key") from err

    @classmethod
    def _expected_alg_and_material_to_key(
        cls,
        alg: KeyAlg,
        public_key_multibase: Optional[str] = None,
        public_key_base58: Optional[str] = None,
    ) -> Key:
        """Convert an Ed25519 key to an Askar Key instance."""
        if public_key_multibase and public_key_base58:
            raise ValueError(
                "Only one of public_key_multibase or public_key_base58 must be given"
            )
        if not public_key_multibase and not public_key_base58:
            raise ValueError(
                "One of public_key_multibase or public_key_base58 must be given)"
            )

        if public_key_multibase:
            decoded = multibase.decode(public_key_multibase)
            if len(decoded) == 32:
                # No multicodec prefix
                try:
                    key = Key.from_public_bytes(alg, decoded)
                except AskarError as err:
                    raise ValueError("Invalid key") from err
                return key
            else:
                key = cls._multikey_to_key(public_key_multibase)
                if key.algorithm != alg:
                    raise ValueError("Type and algorithm mismatch")
                return key

        if public_key_base58:
            decoded = multibase.decode("z" + public_key_base58)
            return Key.from_public_bytes(alg, decoded)

        raise ValueError("Failed to parse key")

    @classmethod
    def from_verification_method(cls, vm: dict) -> "PublicKey":
        """Create a Key instance from a DID Document Verification Method."""
        vm_type = vm.get("type")
        ident = vm.get("id")
        controller = vm.get("controller")
        if not vm_type:
            raise ValueError("Verification method missing type")

        if not ident:
            raise ValueError("Verification method missing id")

        if not controller:
            raise ValueError("Verification method missing controller")

        if ident.startswith("#"):
            kid = f"{controller}#{ident}"
        else:
            kid = ident

        if vm_type == "Multikey":
            multikey = vm.get("publicKeyMultiBase")
            if not multikey:
                raise ValueError("Multikey verification method missing key")

            key = cls._multikey_to_key(multikey)
            return cls(key, kid)

        alg = cls.type_to_alg.get(vm_type)
        if not alg:
            raise ValueError("Unsupported verification method type: {vm_type}")

        base58 = vm.get("publicKeyBase58")
        multi = vm.get("publicKeyMultiBase")
        key = cls._expected_alg_and_material_to_key(
            alg, public_key_base58=base58, public_key_multibase=multi
        )
        return cls(key, kid)

    @property
    def kid(self) -> str:
        """Get the key ID."""
        return self._kid

    @property
    def multikey(self) -> str:
        """Get the key in multibase format."""
        return self._multikey


class AskarSecretKey(SecretKey):
    """Secret key implementation for Askar."""

    def __init__(self, key: Key, kid: str):
        """Initialize a new AskarSecretKey instance."""
        self.key = key
        self._kid = kid

    @property
    def kid(self) -> str:
        """Get the key ID."""
        return self._kid



class AskarCryptoService(CryptoService[AskarKey, AskarSecretKey]):
    """Askar backend for DIDComm Messaging."""

    async def ecdh_es_encrypt(
        self, to_keys: Mapping[str, AskarKey], message: bytes
    ) -> bytes:
        """Encode a message into DIDComm v2 anonymous encryption."""
        builder = JweBuilder(with_flatten_recipients=False)

        alg_id = "ECDH-ES+A256KW"
        enc_id = "XC20P"
        enc_alg = KeyAlg.XC20P
        wrap_alg = KeyAlg.A256KW

        if not to_keys:
            raise ValueError("No message recipients")

        try:
            cek = Key.generate(enc_alg)
        except AskarError:
            raise CryptoServiceError("Error creating content encryption key")

        for kid, recip_key in to_keys.items():
            try:
                epk = Key.generate(recip_key.key.algorithm, ephemeral=True)
            except AskarError:
                raise CryptoServiceError("Error creating ephemeral key")
            enc_key = ecdh.EcdhEs(alg_id, None, None).sender_wrap_key(  # type: ignore
                wrap_alg, epk, recip_key.key, cek
            )
            builder.add_recipient(
                JweRecipient(
                    encrypted_key=enc_key.ciphertext,
                    header={"kid": kid, "epk": epk.get_jwk_public()},
                )
            )

        builder.set_protected(
            OrderedDict(
                [
                    ("alg", alg_id),
                    ("enc", enc_id),
                ]
            )
        )
        try:
            payload = cek.aead_encrypt(message, aad=builder.protected_bytes)
        except AskarError:
            raise CryptoServiceError("Error encrypting message payload")
        builder.set_payload(payload.ciphertext, payload.nonce, payload.tag)

        return builder.build().to_json().encode("utf-8")

    async def ecdh_es_decrypt(
        self,
        wrapper: Union[JweEnvelope, str, bytes],
        recip_key: AskarKey,
    ) -> bytes:
        """Decode a message from DIDComm v2 anonymous encryption."""
        if isinstance(wrapper, bytes):
            wrapper = wrapper.decode("utf-8")
        if not isinstance(wrapper, JweEnvelope):
            wrapper = JweEnvelope.from_json(wrapper)

        alg_id = wrapper.protected.get("alg")

        if alg_id and alg_id in ("ECDH-ES+A128KW", "ECDH-ES+A256KW"):
            wrap_alg = alg_id[8:]
        else:
            raise CryptoServiceError(f"Missing or unsupported ECDH-ES algorithm: {alg_id}")

        recip = wrapper.get_recipient(recip_key.kid)
        if not recip:
            raise CryptoServiceError(f"Recipient header not found: {recip_key.kid}")

        enc_alg = recip.header.get("enc")
        if not enc_alg or enc_alg not in (
            "A128GCM",
            "A256GCM",
            "A128CBC-HS256",
            "A256CBC-HS512",
            "XC20P",
        ):
            raise CryptoServiceError(f"Unsupported ECDH-ES content encryption: {enc_alg}")

        epk_header = recip.header.get("epk")
        if not epk_header:
            raise CryptoServiceError("Missing ephemeral key")

        try:
            epk = Key.from_jwk(epk_header)
        except AskarError:
            raise CryptoServiceError("Error loading ephemeral key")

        apu = recip.header.get("apu")
        apv = recip.header.get("apv")
        # apu and apv are allowed to be None

        try:
            cek = ecdh.EcdhEs(alg_id, apu, apv).receiver_unwrap_key(  # type: ignore
                wrap_alg,
                enc_alg,
                epk,
                recip_key.key,
                recip.encrypted_key,
            )
        except AskarError:
            raise CryptoServiceError("Error decrypting content encryption key")

        try:
            plaintext = cek.aead_decrypt(
                wrapper.ciphertext,
                nonce=wrapper.iv,
                tag=wrapper.tag,
                aad=wrapper.combined_aad,
            )
        except AskarError:
            raise CryptoServiceError("Error decrypting message payload")

        return plaintext

    async def ecdh_1pu_encrypt(
        self,
        to_keys: Mapping[str, AskarKey],
        sender_kid: str,
        sender_key: AskarKey,
        message: bytes,
    ) -> bytes:
        """Encode a message into DIDComm v2 authenticated encryption."""
        builder = JweBuilder(with_flatten_recipients=False)

        alg_id = "ECDH-1PU+A256KW"
        enc_id = "A256CBC-HS512"
        enc_alg = KeyAlg.A256CBC_HS512
        wrap_alg = KeyAlg.A256KW
        agree_alg = sender_key.key.algorithm

        if not to_keys:
            raise CryptoServiceError("No message recipients")

        try:
            cek = Key.generate(enc_alg)
        except AskarError:
            raise CryptoServiceError("Error creating content encryption key")

        try:
            epk = Key.generate(agree_alg, ephemeral=True)
        except AskarError:
            raise CryptoServiceError("Error creating ephemeral key")

        apu = b64url(sender_kid)
        apv = []
        for kid, recip_key in to_keys.items():
            if agree_alg:
                if agree_alg != recip_key.key.algorithm:
                    raise CryptoServiceError("Recipient key types must be consistent")
            else:
                agree_alg = recip_key.key.algorithm
            apv.append(kid)
        apv.sort()
        apv = b64url(".".join(apv))

        builder.set_protected(
            OrderedDict(
                [
                    ("alg", alg_id),
                    ("enc", enc_id),
                    ("apu", apu),
                    ("apv", apv),
                    ("epk", json.loads(epk.get_jwk_public())),
                    ("skid", sender_kid),
                ]
            )
        )
        try:
            payload = cek.aead_encrypt(message, aad=builder.protected_bytes)
        except AskarError:
            raise CryptoServiceError("Error encrypting message payload")
        builder.set_payload(payload.ciphertext, payload.nonce, payload.tag)

        for kid, recip_key in to_keys.items():
            enc_key = ecdh.Ecdh1PU(alg_id, apu, apv).sender_wrap_key(
                wrap_alg, epk, sender_key.key, recip_key.key, cek, cc_tag=payload.tag
            )
            builder.add_recipient(
                JweRecipient(encrypted_key=enc_key.ciphertext, header={"kid": kid})
            )

        return builder.build().to_json().encode("utf-8")

    async def ecdh_1pu_decrypt(
        self,
        wrapper: Union[JweEnvelope, str, bytes],
        recip_key: AskarKey,
        sender_key: AskarKey,
    ):
        """Decode a message from DIDComm v2 authenticated encryption."""
        if isinstance(wrapper, bytes):
            wrapper = wrapper.decode("utf-8")
        if not isinstance(wrapper, JweEnvelope):
            wrapper = JweEnvelope.from_json(wrapper)

        alg_id = wrapper.protected.get("alg")
        if alg_id and alg_id in ("ECDH-1PU+A128KW", "ECDH-1PU+A256KW"):
            wrap_alg = alg_id[9:]
        else:
            raise CryptoServiceError(f"Unsupported ECDH-1PU algorithm: {alg_id}")

        enc_alg = wrapper.protected.get("enc")
        if not enc_alg or enc_alg not in ("A128CBC-HS256", "A256CBC-HS512"):
            raise CryptoServiceError(f"Unsupported ECDH-1PU content encryption: {enc_alg}")

        recip = wrapper.get_recipient(recip_key.kid)
        if not recip:
            raise CryptoServiceError(f"Recipient header not found: {recip_key.kid}")

        epk_header = recip.header.get("epk")
        if not epk_header:
            raise CryptoServiceError("Missing ephemeral key")

        try:
            epk = Key.from_jwk(epk_header)
        except AskarError:
            raise CryptoServiceError("Error loading ephemeral key")

        apu = wrapper.protected.get("apu")
        apv = wrapper.protected.get("apv")
        # apu and apv are allowed to be None

        try:
            cek = ecdh.Ecdh1PU(alg_id, apu, apv).receiver_unwrap_key(  # type: ignore
                wrap_alg,
                enc_alg,
                epk,
                sender_key.key,
                recip_key.key,
                recip.encrypted_key,
                cc_tag=wrapper.tag,
            )
        except AskarError:
            raise CryptoServiceError("Error decrypting content encryption key")

        try:
            plaintext = cek.aead_decrypt(
                wrapper.ciphertext,
                nonce=wrapper.iv,
                tag=wrapper.tag,
                aad=wrapper.combined_aad,
            )
        except AskarError:
            raise CryptoServiceError("Error decrypting message payload")

        return plaintext

class AskarWithStore(KMS, AskarCryptoService, SecretsManager):
    """Askar KMS with an Askar Store for secrets management."""

    def __init__(self, store: Store):
        """Initialize a new Askar instance."""
        self.store = store

    async def fetch_key_by_kid(self, kid: str) -> Optional[AskarSecretKey]:
        """Fetch a public key by key ID."""
        async with self.store.session() as session:
            key_entry = await session.fetch_key(kid)
            if not key_entry:
                return None

        # cached_property doesn't play nice with pyright
        return AskarKey(key_entry.key, kid)  # type: ignore


class AskarDelegatedSecrets(KMS, AskarCryptoService, SecretsManager):
    """Askar KMS with delegated secrets management."""

    def __init__(self, secrets: SecretsManager):
        """Initialize a new AskarDelegatedSecrets instance."""
        self.secrets = secrets

    async def get_secret_by_kid(self, kid: str) -> Optional[SecretKey]:
        """Get a secret key by its kid."""
        return await self.secrets.get_secret_by_kid(kid)
