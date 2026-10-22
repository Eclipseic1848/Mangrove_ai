import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

// 正常发布必须带齐既有品牌资源，不能用组件的异常兜底掩盖漏包。
const originals = {
  "bilibili.png": { bytes: 6378, sha256: "35a8c8dfef2590d18a7dc2d2d46d76e61fdf5569ed54d44887a23e45561fd4f2" },
  "claude-color.png": { bytes: 9337, sha256: "97b53fb85b3bf401ffeb8ee7c5ebad734ce9f6edc3038f242021c53bcc18e23d" },
  "deepseek.png": { bytes: 10501, sha256: "045c46b3f5574d5da24eef7d34aab7c446c7110beb942670026c0aacfb2a117e" },
  "douyin.png": { bytes: 6268, sha256: "c7f68952c0046711a58189be42c172f378315d5cd8ba38badc78ab3610114fb9" },
  "gemini-color.png": { bytes: 19333, sha256: "10c628f55d22a9725b9f9fccce7cf062b9fb68da5f7736e87010e0594d7ba6db" },
  "grok.png": { bytes: 6883, sha256: "f10ec0b2322f9d65a973d3d48fcdc41270d8b123a5d36e2826dabf9dcefb0762" },
  "jd.png": { bytes: 8645, sha256: "deb234336696dcdc415ad8d72f90cbe51031b7a837a830c3d6b9eb0d056db7f5" },
  "kimi-color.png": { bytes: 3470, sha256: "fb460178c19cd28fc953fb446eb200b4050f4046eae3d84d1a15189eb50f717c" },
  "kuaishou.png": { bytes: 4877, sha256: "cbb9920257b26bb3451bfad8f9b98a610763622c6f2015f1628a869b70025bd3" },
  "pdd.png": { bytes: 14541, sha256: "fd38f928bc704cb62fd43a764dbf818315a37feda049441a72c20c90f2efe8b6" },
  "qwen.png": { bytes: 3357, sha256: "7a7c799fcbd20e048331e2019d5f570728af71d7783ca9c3f0527baa1ae907d1" },
  "taobao.png": { bytes: 6731, sha256: "e3b8fcd140974727a17f93869d58228363f596594be730297d3f45547faf7bf5" },
  "tieba.png": { bytes: 4968, sha256: "5795d5864489c339c3e6c528b72f5faf8f50b99f3e111a6eedd98db2f5576412" },
  "weibo.png": { bytes: 11839, sha256: "ba100537977fc7d6edffab23f11885852640c1ddb4b6a04aef8784c3c2a9eb3d" },
  "xiaohongshu.png": { bytes: 5680, sha256: "c27d001f9529aad6e6b79ddbd3e4375a7bc90a0fb2ca9019fa51cac99572e05a" },
  "zhihu.png": { bytes: 8419, sha256: "acd6aa5945271b2e4f789332b1ce9d6440afc0777b151faf871f4fa51485367a" },
  "zhipu-color.png": { bytes: 7823, sha256: "612dca42bba94fd6e760eea79bb34ee8f40f23e24944e58075d6a9ff43b6801a" },
};
const png = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const files = {};
const failures = [];
for (const [name, expected] of Object.entries(originals)) {
  try {
    const source = readFileSync(new URL(`../public/overview-brands/${name}`, import.meta.url));
    const built = readFileSync(new URL(`../dist/overview-brands/${name}`, import.meta.url));
    const sha256 = createHash("sha256").update(source).digest("hex");
    if (!source.subarray(0, 8).equals(png) || source.length !== expected.bytes || sha256 !== expected.sha256 || !built.equals(source)) {
      throw new Error("构建图片不是源文件的完整 PNG 原件");
    }
    files[name] = { bytes: built.length, sha256 };
  } catch (error) {
    failures.push({ name, error: error instanceof Error ? error.message : String(error) });
  }
}
if (failures.length) {
  console.error(JSON.stringify({ ok: false, message: "正式发布缺少或损坏品牌图片", failures }));
  process.exitCode = 1;
} else {
  console.log(JSON.stringify({ ok: true, brand_assets: Object.keys(originals).length, files }));
}
