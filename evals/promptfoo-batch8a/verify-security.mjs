import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
const sharp = require('sharp');
const AdmZip = require('adm-zip');
assert.equal(sharp.versions.sharp, '0.35.4');
assert.equal(require('adm-zip/package.json').version, '0.6.0');
// 真实图片解码与transformers使用的同一sharp路径，不下载模型。
const png = await sharp({ create: { width: 4, height: 3, channels: 3, background: '#ff0000' } }).png().toBuffer();
const { RawImage } = await import('./node_modules/@huggingface/transformers/src/utils/image.js');
const img = await RawImage.fromBlob(new Blob([png], { type: 'image/png' }));
assert.equal(img.width, 4); assert.equal(img.height, 3);
const resized = await img.resize(2, 2);
assert.equal(resized.width, 2); assert.equal(resized.height, 2);
assert.ok(sharp.versions.heif);
// 复用onnx安装器的单文件解压API，但只写合成临时目录。
const archive = new AdmZip(); archive.addFile('native/test.dll', Buffer.from('synthetic-native-file'));
const bytes = archive.toBuffer();
const directory = mkdtempSync(join(tmpdir(), 'mangrove-zip-'));
try { const zip = new AdmZip(bytes); zip.extractEntryTo(zip.getEntry('native/test.dll'), directory, false, true); assert.equal(readFileSync(join(directory, 'test.dll'), 'utf8'), 'synthetic-native-file'); }
finally { unlinkSync(join(directory, 'test.dll')); rmdirSync(directory); }
// 声明4GiB但实际极小；分配守卫使旧漏洞反例也不会真的耗尽内存。
const malformed = Buffer.from(bytes); const central = malformed.indexOf(Buffer.from([0x50,0x4b,0x01,0x02])); assert.ok(central >= 0); malformed.writeUInt32LE(0xffffffff, central + 24);
const allocate = Buffer.alloc; let largest = 0;
Buffer.alloc = function(size, ...args) { largest = Math.max(largest, size); assert.ok(size <= 10*1024*1024, 'unexpected large allocation'); return allocate(size, ...args); };
try { assert.equal(new AdmZip(malformed).getEntry('native/test.dll').getData().toString('utf8'), 'synthetic-native-file'); }
finally { Buffer.alloc = allocate; }
console.log(JSON.stringify({ sharp: sharp.versions, admZip: '0.6.0', transformerImage: 'decoded and resized', extractEntry: 'byte exact', declaredZipSize: 0xffffffff, largestAllocation: largest, externalModelCalls: 0 }));
