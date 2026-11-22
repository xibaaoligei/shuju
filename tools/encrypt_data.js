/*
 * AES-256-GCM + PBKDF2-SHA256 加密。
 * 用法：SITE_PASSPHRASE=... node encrypt_data.js input.json output.json
 *
 * 输出格式与浏览器 WebCrypto 的 AES-GCM 兼容：
 *   data = ciphertext || authTag
 */
"use strict";

const crypto = require("crypto");
const fs = require("fs");

const input = process.argv[2];
const output = process.argv[3];
const passphrase = process.env.SITE_PASSPHRASE || "";

if (!input || !output) {
  console.error("usage: node encrypt_data.js <input.json> <output.json>");
  process.exit(2);
}
if (!passphrase) {
  console.error("SITE_PASSPHRASE is required");
  process.exit(2);
}

const plain = fs.readFileSync(input);
const salt = crypto.randomBytes(16);
const iv = crypto.randomBytes(12);
const iterations = 250000;
const key = crypto.pbkdf2Sync(passphrase, salt, iterations, 32, "sha256");

const cipher = crypto.createCipheriv("aes-256-gcm", key, iv);
const ciphertext = Buffer.concat([cipher.update(plain), cipher.final()]);
const tag = cipher.getAuthTag();

const payload = {
  v: 1,
  alg: "AES-256-GCM",
  kdf: "PBKDF2-SHA256",
  iterations,
  salt: salt.toString("base64"),
  iv: iv.toString("base64"),
  data: Buffer.concat([ciphertext, tag]).toString("base64")
};

fs.writeFileSync(output, JSON.stringify(payload), "utf8");
console.log(`encrypted ${plain.length} bytes -> ${output}`);
