type VerificationResult = {
  ok: boolean;
  detail: string;
  verification?: { scope: string; status: string };
};

export function verificationLabel(result: VerificationResult): string {
  if (result.verification?.scope === "identity") {
    if (result.verification.status === "valid") return "身份验证通过，业务操作尚未验证";
    if (result.verification.status === "invalid") return "登录身份无效，请重新登录后更新凭证";
    return "登录身份尚未确认";
  }
  return result.ok ? "本次检查通过" : "本次检查未通过";
}
