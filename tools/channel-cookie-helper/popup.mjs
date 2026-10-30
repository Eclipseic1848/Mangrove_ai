import { CHANNELS, getChannel } from "./channels.mjs";
import { CookieReadError, ERROR_TEXT, prepareCookieHeader } from "./cookies.mjs";

const element = id => document.getElementById(id);
const channelSelect = element("channel");
const consent = element("consent");
const readButton = element("read");
const copyButton = element("copy");
let header = "";
let busy = false;
let revision = 0;

function clearReading(resetConsent = true) {
  revision += 1;
  header = "";
  busy = false;
  channelSelect.disabled = false;
  copyButton.disabled = true;
  element("reread").disabled = false;
  consent.disabled = false;
  if (resetConsent) consent.checked = false;
  readButton.disabled = !consent.checked;
  readButton.textContent = "授权并读取";
  element("step-2").removeAttribute("aria-busy");
  for (const id of ["summary", "copy-status", "read-error"]) element(id).textContent = "";
  element("read-error").hidden = true;
}

function showStep(step, focus = true) {
  for (let current = 1; current <= 3; current += 1) element(`step-${current}`).hidden = current !== step;
  document.querySelectorAll(".steps li").forEach((item, index) => {
    if (index + 1 === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  if (focus) element(`title-${step}`).focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function currentChannel() {
  return getChannel(channelSelect.value) || CHANNELS[0];
}

function applyChannel() {
  clearReading();
  const channel = currentChannel();
  document.title = `${channel.name}登录助手 · Mangrove`;
  element("channel-image").src = `brands/${channel.image}`;
  element("channel-name").textContent = channel.name;
  element("login-instructions").textContent = `在当前 Chrome 个人资料的普通窗口中登录${channel.name}，确认是你要维护的账号，并完成官网要求的验证。`;
  const official = element("official-login");
  official.href = channel.loginUrl;
  official.textContent = `打开${channel.name}官网 ↗`;
  element("read-target").textContent = channel.cookieUrl;
  element("permission-domains").textContent = `本渠道站点授权：${channel.domains.join("、")}。包含适用于业务请求的父域字段，不读取登录中转站。`;
  element("consent-label").textContent = `我确认当前官方账号正确，并同意本次读取${channel.name}登录态`;
  element("import-instructions").textContent = `返回 Mangrove，将登录态手动粘贴到对应的${channel.name}配置，再按原流程验证和保存。个人配置仅用于本人；平台共享配置由超级管理员维护。`;
  element("channel-note").textContent = channel.note;
  // 原生选择框的方向键需要保留焦点，不能每次换渠道都抢到步骤标题。
  showStep(1, false);
}

for (const channel of CHANNELS) {
  const option = document.createElement("option");
  option.value = channel.id;
  option.textContent = channel.name;
  channelSelect.append(option);
}
channelSelect.addEventListener("change", applyChannel);
element("login-done").addEventListener("click", () => showStep(2));
element("back").addEventListener("click", () => { clearReading(); showStep(1); });
element("reread").addEventListener("click", () => { clearReading(); showStep(2); });
consent.addEventListener("change", () => clearReading(false));

function isCurrent(token, channel) {
  return token === revision && currentChannel().id === channel.id && consent.checked;
}

readButton.addEventListener("click", async () => {
  if (busy || !consent.checked) return;
  clearReading(false);
  const channel = currentChannel();
  const token = revision;
  busy = true;
  readButton.disabled = consent.disabled = true;
  readButton.textContent = "正在授权/读取…";
  element("step-2").setAttribute("aria-busy", "true");
  try {
    // 必须在用户点击调用栈内申请权限；选渠道本身绝不申请或读取。
    const granted = await chrome.permissions.request({ origins: [...channel.origins] });
    if (!isCurrent(token, channel)) return;
    if (!granted || !await chrome.permissions.contains({ origins: [...channel.origins] })) throw new CookieReadError("denied");
    if (!isCurrent(token, channel)) return;
    const rows = await chrome.cookies.getAll({ url: channel.cookieUrl });
    // 切换、取消或关闭后的旧响应不能变成新渠道凭据，也不能重新点亮复制按钮。
    if (!isCurrent(token, channel)) return;
    const result = prepareCookieHeader(channel.id, rows);
    header = result.header;
    element("summary").textContent = `${channel.name}：已读取 ${result.count} 项登录字段，内容已隐藏，仅暂存在本机助手内；身份尚未验证。`;
    copyButton.disabled = false;
    showStep(3);
  } catch (failure) {
    if (!isCurrent(token, channel)) return;
    header = "";
    copyButton.disabled = true;
    const error = element("read-error");
    error.textContent = ERROR_TEXT[failure instanceof CookieReadError ? failure.code : "unreadable"] || ERROR_TEXT.unreadable;
    error.hidden = false;
  } finally {
    if (token === revision) {
      busy = false;
      consent.disabled = false;
      readButton.disabled = !consent.checked;
      readButton.textContent = "授权并读取";
      element("step-2").removeAttribute("aria-busy");
    }
  }
});

copyButton.addEventListener("click", async () => {
  if (busy || !header) return;
  busy = true;
  const token = revision;
  const channel = currentChannel();
  copyButton.disabled = element("reread").disabled = channelSelect.disabled = true;
  try {
    await navigator.clipboard.writeText(header);
    if (isCurrent(token, channel)) element("copy-status").textContent = `已复制${channel.name}登录态，请手动粘贴到同渠道配置，按原流程验证并保存；完成后清理剪贴板。`;
  } catch {
    if (isCurrent(token, channel)) element("copy-status").textContent = "复制失败，请允许浏览器使用剪贴板后重试。助手不会明文显示登录态。";
  } finally {
    if (token === revision) {
      busy = false;
      copyButton.disabled = !header;
      element("reread").disabled = channelSelect.disabled = false;
    }
  }
});
window.addEventListener("pagehide", () => clearReading());
applyChannel();
