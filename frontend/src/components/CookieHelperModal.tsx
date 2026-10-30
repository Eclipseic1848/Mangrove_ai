import { useEffect, useRef, useState } from "react";
import { Download, ExternalLink, Info, Loader2, Search, ShieldCheck, UserRound, X } from "lucide-react";
import { OptionalBrandImage } from "@/components/ProviderLogo";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { downloadFile } from "@/lib/api";
import { cn } from "@/lib/utils";
import { COOKIE_HELPER_CHANNELS, type CookieHelperChannelKey } from "@/lib/cookieHelperChannels";

const STEPS = ["官方登录", "本机读取", "确认导入"];

export function CookieHelperModal({ open, channelKey, scope, onClose, onConfigure }: {
  open: boolean;
  channelKey: CookieHelperChannelKey;
  scope: "personal" | "platform";
  onClose: () => void;
  onConfigure: () => void;
}) {
  const [step, setStep] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const download = useRef<AbortController | null>(null);
  const stepContent = useRef<HTMLElement | null>(null);
  const platform = scope === "platform";
  const channel = COOKIE_HELPER_CHANNELS[channelKey];

  useEffect(() => {
    download.current?.abort();
    download.current = null;
    setStep(0); setConfirmed(false); setDownloading(false); setNotice(""); setError("");
    return () => { download.current?.abort(); download.current = null; };
  }, [open, channelKey]);

  function changeStep(next: number) {
    setStep(next);
    // 最后一步会移除“下一步”按钮，把焦点留在弹窗内以保留键盘与朗读顺序。
    requestAnimationFrame(() => {
      stepContent.current?.focus({ preventScroll: true });
      stepContent.current?.closest('[role="dialog"]')?.scrollTo({ top: 0 });
    });
  }

  async function downloadHelper() {
    if (download.current) return;
    const controller = new AbortController();
    download.current = controller;
    setDownloading(true); setError(""); setNotice("");
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 30_000);
    try {
      await downloadFile("/api/config/cookie-helper/download", "mangrove-channel-cookie-helper.zip", controller.signal, "application/zip");
      if (!controller.signal.aborted) setNotice("已发起安装包下载。保存并解压后，在 Chrome 中加载扩展。");
    } catch {
      if (download.current === controller && (timedOut || !controller.signal.aborted)) {
        setError(timedOut ? "安装包下载超时，请检查网络后重试。" : "安装包下载失败，请核对登录状态或网络后重试。");
      }
    } finally {
      window.clearTimeout(timeout);
      if (download.current === controller) { download.current = null; setDownloading(false); }
    }
  }

  return <Modal open={open} onClose={onClose} title={platform ? `平台${channel.name}登录助手` : `我的${channel.name}登录助手`} headingLevel={2} wide>
    <div className="space-y-5">
      <div className="flex items-start gap-3 border-b pb-4">
        <OptionalBrandImage src={`/overview-brands/${channel.logo}`} className="h-11 w-11 shrink-0 rounded-lg object-contain" />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm text-muted-foreground">{platform ? "更新平台共享采集账号，供使用该共享账号的任务使用。" : "更新自己的采集登录态，仅供你的任务使用。"}</p>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-1 text-xs text-accent-foreground">
            {platform ? <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5" /> : <UserRound aria-hidden="true" className="h-3.5 w-3.5" />}
            {platform ? "平台共享 · 超级管理员维护" : "个人凭据 · 仅自己可用"}
          </span>
        </div>
        <Button type="button" variant="ghost" size="icon" aria-label={`关闭${channel.name}登录助手`} className="shrink-0" onClick={onClose}><X aria-hidden="true" /></Button>
      </div>

      <ol aria-label="登录助手步骤" className="grid grid-cols-3 gap-2">
        {STEPS.map((label, index) => <li key={label} aria-current={step === index ? "step" : undefined} className="flex flex-col items-center gap-2 text-center text-xs sm:text-sm">
          <span className={cn("flex h-9 w-9 items-center justify-center rounded-full border font-semibold", step === index ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground")}>{index + 1}</span>
          <span className={step === index ? "font-medium text-primary" : "text-muted-foreground"}>{label}</span>
        </li>)}
      </ol>

      <section ref={stepContent} tabIndex={-1} aria-label={STEPS[step]} className="space-y-4 focus:outline-none">
        {step === 0 && <>
          <div className="space-y-2">
            <h3 className="font-semibold">先在本机安装助手，再正常登录{channel.name}</h3>
            <p className="text-sm leading-relaxed text-muted-foreground">一个助手覆盖现有采集渠道，首次使用下载一次即可。请在本机助手中选择“{channel.name}”，仅在你主动授权后读取该渠道 Cookie，不接收密码，不自动上传。</p>
          </div>
          <div className="rounded-lg border bg-muted/20 p-4 space-y-3">
            <Button type="button" variant="outline" className="min-w-36" disabled={downloading} onClick={() => void downloadHelper()}>
              {downloading ? <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> : <Download aria-hidden="true" />}
              {downloading ? "下载中…" : "下载本机助手"}
            </Button>
            <ol className="list-decimal space-y-2 pl-5 text-sm leading-relaxed">
              <li>解压安装包，保留其中的文件。</li>
              <li>在本机 Chrome 地址栏输入 <code className="break-all rounded bg-muted px-1">chrome://extensions</code>，开启“开发者模式”。</li>
              <li>选择“加载已解压的扩展程序”，选中解压后含 <code>manifest.json</code> 的目录。</li>
            </ol>
          </div>
          {notice && <p role="status" className="text-sm text-primary">{notice}</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <p className="text-sm leading-relaxed">{platform ? `请在${channel.loginName}官方页面登录待维护的平台共享账号。` : `请在${channel.loginName}官方页面登录你自己的账号。`}密码与平台要求的验证都在官方页面完成。</p>
          {channelKey === "tb_cookie" && <p className="text-sm text-muted-foreground">淘宝和天猫共用此配置；淘宝身份检查通过不代表天猫访问已验证。</p>}
          {channelKey === "pdd_cookie" && <p className="text-sm text-muted-foreground">该渠道的独立身份状态可能仍显示“尚未确认”，不据此判断 Cookie 已失效。</p>}
          <a href={channel.loginUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1.5 rounded text-sm text-primary underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><ExternalLink aria-hidden="true" className="h-4 w-4" />打开{channel.loginName}官方页面</a>
        </>}
        {step === 1 && <>
          <h3 className="font-semibold">在本机扩展中读取并复制</h3>
          <div className="flex items-start gap-2 rounded-lg border border-primary/25 bg-primary/5 p-3 text-sm leading-relaxed"><Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><p>本页不会连接或读取你的 Chrome。请点击浏览器右上角的“扩展程序”，打开“Mangrove 登录助手”并选择“{channel.name}”。网页不会自动同步扩展中的渠道。</p></div>
          <ol className="list-decimal space-y-3 pl-5 text-sm leading-relaxed">
            <li>先在{channel.loginName}官方页面核对当前账号，确认是{platform ? "要维护的平台共享账号" : "你自己的账号"}。</li>
            <li>在本机助手中核对所选渠道为“{channel.name}”，主动读取最新登录态，并按提示授权该渠道站点。拒绝授权时不会读取。未登录或需要验证时，返回官方页面完成。</li>
            <li>在助手中核对用途后点击复制 Cookie，再回到本页。不要将 Cookie 发给聊天、模型或他人。</li>
          </ol>
          <p className="text-sm text-muted-foreground">读取到 Cookie 只表示本机存在凭据，不代表服务器已确认登录身份或搜索访问。</p>
        </>}
        {step === 2 && <>
          <h3 className="font-semibold">核对用途后，手动粘贴到原配置</h3>
          <p className="text-sm leading-relaxed">{platform ? `接下来打开平台共享${channel.name}配置。保存会影响后续使用该共享账号的任务，个人凭据保持不变。` : `接下来打开“我的采集账号”中的${channel.name}配置，只保存到当前登录用户，不修改平台共享凭据。`}</p>
          <label className="flex items-start gap-2 rounded-lg border p-3 text-sm leading-relaxed"><input className="mt-1 h-4 w-4 shrink-0 accent-primary" type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /><span>我已核对{channel.name}当前账号和上述用途，并已在本机助手中复制 Cookie。</span></label>
          <p className="text-sm text-muted-foreground">进入后手动粘贴，再按原流程选择保存或验证。此助手不会替你提交配置、验证身份或恢复旧任务。</p>
        </>}
      </section>

      <div className="flex items-start gap-2 rounded-lg border bg-muted/20 p-3 text-sm leading-relaxed"><ShieldCheck aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><p>密码只在{channel.loginName}官方页面输入。复制的 Cookie 属于登录凭据；用完后请及时清理系统剪贴板。</p></div>
      <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={onClose}>取消</Button>
        {step > 0 && <Button type="button" variant="outline" onClick={() => changeStep(step - 1)}>上一步</Button>}
        {step < 2 ? <Button type="button" onClick={() => changeStep(step + 1)}>下一步</Button> : <Button type="button" disabled={!confirmed} onClick={onConfigure}>{platform ? "进入平台配置" : "进入我的配置"}</Button>}
      </div>
      <div className="flex flex-wrap justify-between gap-x-4 gap-y-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5"><ShieldCheck aria-hidden="true" className="h-4 w-4" />基础身份：未验证</span>
        <span className="inline-flex items-center gap-1.5"><Search aria-hidden="true" className="h-4 w-4" />{channelKey === "mc_cookie_xhs" ? "搜索访问" : "业务访问"}：未验证</span>
      </div>
    </div>
  </Modal>;
}
