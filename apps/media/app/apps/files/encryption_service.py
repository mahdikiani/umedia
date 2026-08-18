"""Encryption service for handling file encryption and decryption operations."""

import os
from collections.abc import AsyncGenerator

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from singleton import Singleton

from .storage_backend.base_storage import StorageBackend


class EncryptionService(metaclass=Singleton):
    """Service responsible for file encryption and decryption operations."""

    def __init__(self, backend: StorageBackend) -> None:
        self.backend = backend

    async def stream_and_encrypt(
        self, key: str, encryption_key: bytes, **kwargs: object
    ) -> AsyncGenerator[bytes]:
        """
        Stream file from StorageBackend and encrypt it on the fly.

        Args:
            key: object key
            encryption_key: Encryption key for AES encryption
            **kwargs: Additional parameters

        Yields:
            bytes: Encrypted data chunks (first chunk is IV)
        """
        iv = self.generate_iv()

        encryption_key = (
            encryption_key
            if isinstance(encryption_key, bytes)
            else encryption_key.encode()
        )
        # Create AES-CTR cipher
        cipher = Cipher(
            algorithms.AES(encryption_key), modes.CTR(iv), backend=default_backend()
        )
        encryptor = cipher.encryptor()

        # First, yield the IV since it's needed for decryption
        yield iv

        async for chunk in self.backend.stream_file(key):
            # Encrypt the chunk
            encrypted_chunk = encryptor.update(chunk)
            yield encrypted_chunk

        final_chunk = encryptor.finalize()
        if final_chunk:
            yield final_chunk

    @staticmethod
    async def decrypt_stream(
        encryption_key: bytes, encrypted_stream: AsyncGenerator[bytes]
    ) -> AsyncGenerator[bytes]:
        """
        Decrypt an encrypted stream.

        Args:
            encryption_key: Decryption key for AES decryption
            encrypted_stream: Async generator yielding encrypted chunks

        Yields:
            bytes: Decrypted data chunks
        """
        # Read the IV from the stream first
        iv = await anext(encrypted_stream)
        encryption_key = (
            encryption_key
            if isinstance(encryption_key, bytes)
            else encryption_key.encode()
        )

        # Create AES-CTR cipher with the IV
        cipher = Cipher(
            algorithms.AES(encryption_key), modes.CTR(iv), backend=default_backend()
        )
        decryptor = cipher.decryptor()

        async for encrypted_chunk in encrypted_stream:
            # Decrypt the chunk
            decrypted_chunk = decryptor.update(encrypted_chunk)
            yield decrypted_chunk

        # Finalize decryption
        decrypted_chunk = decryptor.finalize()
        if decrypted_chunk:
            yield decrypted_chunk

    @classmethod
    def create_cipher(cls, encryption_key: bytes, iv: bytes) -> Cipher:
        """
        Create AES-CTR cipher with given key and IV.

        Args:
            encryption_key: Encryption/decryption key
            iv: Initialization vector

        Returns:
            Cipher: Configured AES-CTR cipher
        """
        encryption_key = (
            encryption_key
            if isinstance(encryption_key, bytes)
            else encryption_key.encode()
        )
        return Cipher(
            algorithms.AES(encryption_key), modes.CTR(iv), backend=default_backend()
        )

    @classmethod
    def generate_iv(cls) -> bytes:
        """
        Generate random initialization vector.

        Returns:
            bytes: 16-byte random IV
        """
        return os.urandom(16)
