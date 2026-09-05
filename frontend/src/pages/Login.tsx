import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { MessagesSquare, Database, ShieldCheck, KeyRound, Repeat, Sparkles, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { ApiError } from "@/lib/api";

// 登录页先说明用户能解决什么问题；实现名词只作为可信度补充，避免用技术堆栈代替产品价值。
const FEATURES = [
  { icon: MessagesSquare, title: "对话式全网采集", desc: "自然语言定义目标，自动澄清范围、规划来源，并按站点能力选择采集路径" },
  { icon: Database, title: "多格式数据工作台", desc: "处理 PDF、Word、Excel 与 CSV，保留原件预览、执行进度和任务版本" },
  { icon: ShieldCheck, title: "证据绑定与独立验证", desc: "结果关联来源页码与字段证据，由独立验证器复核覆盖、边界和完整性" },
  { icon: KeyRound, title: "多模型与权限治理", desc: "支持个人 Key、平台共享及本地/LAN 模型，连接与任务按用户隔离" },
  { icon: Repeat, title: "自动化与经验复用", desc: "定时任务持续运行，模板、个人记忆和失败教训帮助团队复用经验" },
];

export function Login() {
  const { login, register } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username, password);
        navigate("/");
      } else {
        const r = await register(username, password, displayName);
        if (r.pending) {
          // 注册待审批：不进入平台，提示并切回登录
          toast.success(r.message || "注册成功，待管理员审批通过后即可登录");
          setMode("login");
          setPassword("");
        } else {
          navigate("/"); // 兼容已激活账号的响应。
        }
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {/* 左侧品牌 / 介绍面板（窄屏隐藏）：深色主题整体压暗，与右侧同步翻转 */}
      <div className="relative hidden w-[52%] flex-col justify-between overflow-hidden bg-gradient-to-br from-primary/95 via-primary to-[hsl(184_75%_22%)] p-12 text-white dark:from-[hsl(185_45%_15%)] dark:via-[hsl(190_50%_11%)] dark:to-[hsl(200_45%_8%)] lg:flex">
        <div className="pointer-events-none absolute inset-0">
          <div className="absolute -right-24 top-10 h-72 w-72 rounded-full bg-white/10 blur-3xl" />
          <div className="absolute bottom-0 left-1/4 h-80 w-80 rounded-full bg-[hsl(160_80%_40%)]/20 blur-3xl" />
        </div>

        <div className="relative flex items-center gap-3">
          <img src="/logo.svg" alt="Mangrove" className="h-10 w-10" />
          <div>
            <div className="text-lg font-semibold tracking-tight">howso@Mangrove</div>
            <div className="text-xs text-white/70">智能数据任务平台</div>
          </div>
        </div>

        <div className="relative max-w-md">
          <div className="mb-5 inline-flex items-center gap-1.5 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium backdrop-blur-sm">
            <Sparkles className="h-3.5 w-3.5" /> Agentic Runtime · 可验证执行 · 多模型治理
          </div>
          <h2 className="text-[28px] font-semibold leading-snug tracking-tight">
            从全网采集到文件理解，<br />让数据任务可执行、可验证、可追溯
          </h2>
          <p className="mt-3 text-sm leading-relaxed text-white/75">
            用自然语言描述目标，Mangrove 负责理解要求、选择来源与工具、执行处理并绑定证据；
            通过任务版本、权限隔离和独立验证，让采集与文件处理结果更容易复核和复用。
          </p>

          <div className="mt-7 space-y-3.5">
            {FEATURES.map((f) => (
              <div key={f.title} className="flex items-start gap-3.5">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white/12 ring-1 ring-white/15">
                  <f.icon className="h-[18px] w-[18px]" />
                </div>
                <div className="pt-0.5">
                  <div className="text-sm font-medium">{f.title}</div>
                  <div className="mt-0.5 text-xs leading-snug text-white/65">{f.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex flex-wrap gap-x-4 gap-y-1 text-xs text-white/55">
          <span>市场与竞品情报</span>
          <span>财务与文档审阅</span>
          <span>招投标与政策研究</span>
          <span>舆情与内容分析</span>
        </div>
      </div>

      {/* 右侧登录 */}
      <div className="relative flex flex-1 items-center justify-center px-6">
        {/* 顶部：出品方 LOGO（左，按主题变色，无边框）+ 主题切换（右） */}
        <img
          src="/howso-logo-full.png"
          alt="南京华苏科技有限公司"
          className="absolute left-9 top-8 h-14 w-auto dark:brightness-0 dark:invert"
        />
        <button
          onClick={toggle}
          className="absolute right-8 top-8 flex h-9 w-9 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          title={theme === "dark" ? "切换浅色" : "切换深色"}
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>

        <div className="pointer-events-none absolute inset-0 overflow-hidden lg:hidden">
          <div className="absolute -top-32 left-1/2 h-[420px] w-[620px] -translate-x-1/2 rounded-full bg-primary/10 blur-[120px]" />
        </div>

        <div className="relative w-full max-w-sm animate-fade-in">
          {/* 窄屏顶部品牌（左面板隐藏时） */}
          <div className="mb-7 flex flex-col items-center text-center lg:hidden">
            <img src="/logo.svg" alt="howso@Mangrove" className="mb-3 h-12 w-12" />
            <h1 className="text-xl font-semibold tracking-tight">howso@Mangrove</h1>
            <p className="mt-1 text-sm text-muted-foreground">智能数据任务平台</p>
          </div>

          <div className="mb-6 hidden lg:block">
            <h1 className="text-2xl font-semibold tracking-tight">欢迎回来</h1>
            <p className="mt-1 text-sm text-muted-foreground">登录以继续你的采集、文件处理与自动化任务</p>
          </div>

          <form onSubmit={submit} className="space-y-3 rounded-xl border border-border bg-card p-6 shadow-sm">
            <div className="mb-2 flex gap-1 rounded-lg bg-muted p-1 text-sm">
              {(["login", "register"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={
                    "flex-1 rounded-md py-1.5 font-medium transition-colors " +
                    (mode === m ? "bg-card text-foreground shadow-sm" : "text-muted-foreground")
                  }
                >
                  {m === "login" ? "登录" : "注册"}
                </button>
              ))}
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">用户名</label>
              <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="至少 2 位" autoFocus />
            </div>
            {mode === "register" && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-muted-foreground">显示名（可选）</label>
                <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="如 张三" />
              </div>
            )}
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted-foreground">密码</label>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="至少 6 位"
              />
            </div>

            <Button type="submit" className="w-full" disabled={busy || !username || !password}>
              {busy ? "处理中…" : mode === "login" ? "登录" : "提交注册"}
            </Button>

            <p className="flex items-center justify-center gap-1.5 pt-1 text-xs text-muted-foreground">
              <Sparkles className="h-3 w-3" /> 多用户隔离 · 模型连接与任务按账号区分
            </p>
          </form>

          <p className="mt-6 text-center text-xs text-muted-foreground">
            © {new Date().getFullYear()} 南京华苏科技有限公司 · HOWSO TECHNOLOGY
          </p>
        </div>
      </div>
    </div>
  );
}
