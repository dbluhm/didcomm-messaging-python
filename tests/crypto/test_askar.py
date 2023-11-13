from aries_askar import Key, KeyAlg
from pydid import VerificationMethod
import pytest

from didcomm_messaging.crypto.backend.askar import (
    AskarCryptoService,
    AskarKey,
    AskarSecretKey,
)


ALICE_KID = "did:example:alice#key-1"
BOB_KID = "did:example:bob#key-1"
CAROL_KID = "did:example:carol#key-2"
MESSAGE = b"Expecto patronum"


@pytest.fixture
def crypto():
    yield AskarCryptoService()


@pytest.mark.asyncio
async def test_1pu_round_trip(crypto: AskarCryptoService):
    """Authcrypt."""
    alg = KeyAlg.X25519
    alice_sk = Key.generate(alg)
    alice_pk = Key.from_jwk(alice_sk.get_jwk_public())
    bob_sk = Key.generate(alg)
    bob_pk = Key.from_jwk(bob_sk.get_jwk_public())
    bob_key = AskarKey(bob_sk, BOB_KID)
    bob_priv_key = AskarSecretKey(bob_sk, BOB_KID)
    alice_key = AskarKey(alice_sk, ALICE_KID)
    alice_priv_key = AskarSecretKey(alice_sk, ALICE_KID)

    enc_message = await crypto.ecdh_1pu_encrypt([bob_key], alice_priv_key, MESSAGE)

    plaintext = await crypto.ecdh_1pu_decrypt(enc_message, bob_priv_key, alice_key)
    assert plaintext == MESSAGE


@pytest.mark.asyncio
async def test_es_round_trip(crypto: AskarCryptoService):
    """Test ECDH-ES (Anoncrypt) round trip."""
    alg = KeyAlg.X25519
    alice_sk = Key.generate(alg)
    alice_pk = Key.from_jwk(alice_sk.get_jwk_public())
    bob_sk = Key.generate(alg)
    bob_pk = Key.from_jwk(bob_sk.get_jwk_public())
    bob_key = AskarKey(bob_sk, BOB_KID)
    bob_priv_key = AskarSecretKey(bob_sk, BOB_KID)
    alice_key = AskarKey(alice_sk, ALICE_KID)
    alice_priv_key = AskarSecretKey(alice_sk, ALICE_KID)

    enc_message = await crypto.ecdh_es_encrypt([bob_key], MESSAGE)

    plaintext = await crypto.ecdh_es_decrypt(enc_message, bob_priv_key)
    assert plaintext == MESSAGE


# to test store/secrets manager stuff
# store = await Store.open("sqlite::memory:")
#     async with store.session as session():
#         await session.insert_key("kid", alice_sk)


# TODO @pytest.mark.parametrize for the different dicts? See below for more examples
@pytest.mark.asyncio
async def test_key_from_verification_method():
    vm = VerificationMethod.deserialize(
        {
            "id": "#6LSqPZfn",
            "type": "X25519KeyAgreementKey2020",
            "publicKeyMultibase": "z6LSqPZfn9krvgXma2icTMKf2uVcYhKXsudCmPoUzqGYW24U",
            "controller": "did:example:123",
        }
    )

    key = AskarCryptoService.verification_method_to_public_key(vm)
    print(key.kid)
    assert type(key) == AskarKey
    assert key.kid.startswith("did:example:123")


# Verification Methods
# {
#   "id": "#6LSqPZfn",
#   "type": "X25519KeyAgreementKey2020",
#   "publicKeyMultibase": "z6LSqPZfn9krvgXma2icTMKf2uVcYhKXsudCmPoUzqGYW24U"
# },
# {
#   "id": "#6MkrCD1c",
#   "type": "Ed25519VerificationKey2020",
#   "publicKeyMultibase": "z6MkrCD1csqtgdj8sjrsu8jxcbeyP6m7LiK87NzhfWqio5yr"
# }

# {
#   "id": "#1",
#   "type": "Ed25519VerificationKey2018",
#   "publicKeyBase58": "AU2FFjtkVzjFuirgWieqGGqtNrAZWS9LDuB8TDp6EUrG"
# }

# {
#   "id": "#1",
#   "type": "Ed25519VerificationKey2018",
#   "publicKeyBase58": "3dtu2WWtd5ELwRTJEPzmEJUYEp8Qq36N2QA24g9tFXK9",
#   "controller": "did:peer:4zQmRMVzDUXhV64pfw3vFaDvyExjzW9oBXCF2n4zYCaHQFAT:zMx3zwMnDECV3GiFs8nmHr38TziMEEkcgFBEDH5PXQ8hxnMrwNfB9wTwskpJMjggeg8NF1jeDSK5772op2zLLdy8TGFCEiYQxpUvvku8qSCZx5Q8V9Li9mDp6WEqGabXLQ9GTinmyQHQyJ6TcfbHaTtJUFjHS962LFPdUwv3aDK673Pci2doTyHVTAsw4m5eToS2dKbtix9f7HNxwvixnbQucWNAWVAF6HTxFYRYmrRPDmeE8n7V1fXFkY7yvR6BWxKiWwHd8Vb1TbBBRStf5niRM2dUAyjJorTstPWSfG2pN5DsRF81NUd7Aif4EhNAQEJCTuAHxQ3rCnNkb9Pf7YTTxbt1t25YgDMioDi4uFhYcnTbHj7D74yNPC2Cfk6WasU69hMxj7Wxro58vtkA6hvDWGtnDyX4PzntBp3fn62R25HW2jadsZMiJpm5ufpYSktEFEHX6gGeF4KPgyU8b2hhyS3FKL4DULYLB6d6CZqUpwrJesGfDtFjfG1btbdmjd6Lm7FCbL3fU9E3AJWEmnFkg16vARiQ1CrzeS9SyNtybKCk4"
# }

# {
#   "id": "#z6MkrmNwty5ajKtFqc1U48oL2MMLjWjartwc5sf2AihZwXDN",
#   "type": "Ed25519VerificationKey2018",
#   "publicKeyBase58": "DK7uJiq9PnPnj7AmNZqVBFoLuwTjT1hFPrk6LSjZ2JRz"
# }

# Here's a case that we probably don't support right now:
# {
#   "type": "Ed25519VerificationKey2018",
#   "publicKeyJwk": {
#     "kty": "OKP",
#     "crv": "Ed25519",
#     "x": "UTBElpNSZB8dS_R9rzWnWB-ozdtL7Sz96RQZhwnzur8"
#   },
#   "id": "#z6MkjvBkt8ETnxXGBFPSGgYKb43q7oNHLX8BiYSPcXVG6gY6"
# }
