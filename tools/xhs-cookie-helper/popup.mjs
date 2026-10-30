import {COOKIE_URL, CookieReadError, ERROR_TEXT, prepareCookieHeader} from "./cookies.mjs";

const element = (id) => document.getElementById(id);
const consent = element("consent");
const readButton = element("read");
const copyButton = element("copy");
let header = "";
let busy = false;

function clearReading() {
  header = "";
  copyButton.disabled = true;
  element("summary").textContent = "";
  element("copy-status").textContent = "";
}

function showStep(step) {
  for (let current = 1; current <= 3; current += 1) element(`step-${current}`).hidden = current !== step;
  document.querySelectorAll(".steps li").forEach((item, index) => {
    if (index + 1 === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  element(`title-${step}`).focus();
}

element("login-done").addEventListener("click", () => showStep(2));
element("back").addEventListener("click", () => {
  clearReading();
  consent.checked = false;
  readButton.disabled = true;
  showStep(1);
});
consent.addEventListener("change", () => {
  clearReading();
  readButton.disabled = !consent.checked;
});
readButton.addEventListener("click", async () => {
  if (busy || !consent.checked) return;
  clearReading();
  busy = true;
  readButton.disabled = consent.disabled = element("back").disabled = true;
  readButton.textContent = "正在读取…";
  element("step-2").setAttribute("aria-busy", "true");
  const error = element("read-error");
  error.hidden = true;
  error.textContent = "";
  try {
    const result = prepareCookieHeader(await chrome.cookies.getAll({url: COOKIE_URL}));
    header = result.header;
    element("summary").textContent = `已读取 ${result.count} 项登录字段，内容已隐藏，仅暂存在本机助手内。`;
    copyButton.disabled = false;
    showStep(3);
  } catch (failure) {
    clearReading();
    error.textContent = ERROR_TEXT[failure instanceof CookieReadError ? failure.code : "unreadable"] || ERROR_TEXT.unreadable;
    error.hidden = false;
  } finally {
    busy = false;
    consent.disabled = element("back").disabled = false;
    readButton.disabled = !consent.checked;
    readButton.textContent = "读取最新登录态";
    element("step-2").removeAttribute("aria-busy");
  }
});
copyButton.addEventListener("click", async () => {
  if (busy || !header) return;
  busy = true;
  copyButton.disabled = element("reread").disabled = true;
  try {
    await navigator.clipboard.writeText(header);
    element("copy-status").textContent = "已复制，请返回 Mangrove 手动粘贴、保存并验证；完成后清理剪贴板。";
  } catch {
    element("copy-status").textContent = "复制失败，请允许浏览器使用剪贴板后重试。助手不会明文显示登录态。";
  } finally {
    busy = false;
    copyButton.disabled = !header;
    element("reread").disabled = false;
  }
});
element("reread").addEventListener("click", () => {
  clearReading();
  consent.checked = false;
  readButton.disabled = true;
  showStep(2);
});
window.addEventListener("pagehide", clearReading);
