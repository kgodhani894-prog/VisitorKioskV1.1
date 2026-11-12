package com.eliterf.kiosk.security

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

object PinAuthCore {
    private const val ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

    fun validPin(pin: String) = pin.matches(Regex("\\d{4,12}"))

    fun recoveryCode(random: SecureRandom): String = (1..24).map { ALPHABET[random.nextInt(ALPHABET.length)] }.joinToString("")

    fun newSalt(random: SecureRandom): ByteArray = ByteArray(16).also(random::nextBytes)

    fun encode(bytes: ByteArray): String = Base64.getEncoder().withoutPadding().encodeToString(bytes)
    fun decode(value: String): ByteArray = Base64.getDecoder().decode(value)

    fun hash(value: String, salt: ByteArray): ByteArray {
        val spec = PBEKeySpec(value.toCharArray(), salt, 210_000, 256)
        return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded
    }

    fun matches(value: String, salt: ByteArray, expected: ByteArray): Boolean = MessageDigest.isEqual(hash(value, salt), expected)
}
