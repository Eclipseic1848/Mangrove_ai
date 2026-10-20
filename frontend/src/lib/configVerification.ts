type VerificationResult = {
  ok: boolean;
  detail: string;
  verification?: { scope: string; status: string; reason?: string };
};

export function verificationLabel(result: VerificationResult): string {
  if (result.verification?.scope === "identity") {
    if (result.verification.status === "valid") return "身份验证通过，业务操作尚未验证";
    if (result.verification.status === "invalid") return "登录身份无效，请重新登录后更新凭证";
    return "登录身份尚未确认";
  }
  return result.ok ? "本次检查通过" : "本次检查未通过";
}

export function reauthenticationFeedback(result: VerificationResult): { failed: boolean; message: string } {
  if (result.verification?.scope === "identity" && result.verification.status === "invalid") {
    return { failed: true, message: "服务器确认当前请求未登录。请重新登录并复制完整 Cookie；原配置未修改。" };
  }
  // 未确认身份不能等同凭证失效；只用有限分类生成提示，不显示第三方响应或凭证。
  const reasons: Record<string, string> = {
    identity_probe_unsupported: "该平台暂不支持独立身份验证，不能判断 Cookie 是否有效。",
    network_error: "服务器网络请求未完成，不能判断 Cookie 是否有效。",
    challenge_required: "平台要求额外验证，服务器尚未确认登录身份。",
    rate_limited: "平台暂时限制请求频率，请稍后检查。",
    access_denied: "平台拒绝了服务器请求，不能据此认定 Cookie 失效。",
    collector_unavailable: "服务器采集组件暂不可用，请先检查采集服务。",
    credential_missing: "未取得可读取的 Cookie，请核对输入内容。",
    credential_scope_incomplete: "当前身份接口未确认所需的业务域会话，请核对该平台的配置说明。",
    credential_changed: "验证期间凭证或服务器环境已变化，请重新检查。",
  };
  const reason = result.verification?.scope === "identity" ? result.verification.reason || "" : "";
  const message = Object.prototype.hasOwnProperty.call(reasons, reason) ? reasons[reason] : undefined;
  return { failed: false, message: `${message || "服务器尚未取得明确的登录身份，不能判断 Cookie 是否有效。"}原配置未修改；可使用“保存配置”保存，状态保持尚未验证。` };
}
