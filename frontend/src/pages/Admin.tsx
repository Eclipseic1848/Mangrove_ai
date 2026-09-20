import { useEffect, useRef, useState } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import {
  Users, RefreshCw, Shield, ShieldOff, KeyRound, Trash2, UserPlus, Ban, CheckCircle2, UserCheck, Clock, Pencil,
  Search,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Pagination } from "@/components/ui/pagination";
import { beijingTime } from "@/lib/beijingTime";
import { api, ApiError } from "@/lib/api";
import { useAuth, roleLevel, roleLabel, ROLE_LEVEL } from "@/lib/auth";

interface ExecutionHold {
  operation_id: string;
  generation: number;
  status: "processing" | "completed" | "failed";
  affected_count: number;
  pending_count: number;
  error_code: string | null;
  retryable: boolean;
  updated_at: string;
}

const holdErrors: Record<string, string> = {
  resource_cleanup_pending: "资源清理尚未确认。",
  worker_unavailable: "执行服务暂不可用。",
  state_read_failed: "执行状态暂时无法核对。",
};

interface AdminUser {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  disabled: number;
  pending: number;
  created_at: string;
  execution_hold: ExecutionHold | null;
}

const ROLE_FILTER_OPTIONS = ["", "super_admin", "admin", "user"] as const;
const STATUS_FILTER_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "normal", label: "正常" },
  { value: "disabled", label: "已禁用" },
  { value: "pending", label: "待审批" },
] as const;

export function Admin() {
  const { user: me } = useAuth();
  const myLevel = roleLevel(me?.role);
  const canSetRole = myLevel > ROLE_LEVEL.admin; // 仅超级管理员可调整 管理员/普通用户 角色
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [pollError, setPollError] = useState(false);
  const [accountErrors, setAccountErrors] = useState<Record<string, string>>({});
  const [changing, setChanging] = useState<Set<string>>(new Set());
  const changingRef = useRef(new Set<string>());
  const listVersion = useRef(0);
  const quietRefresh = useRef(false);
  const [refresh, setRefresh] = useState(0);
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  const [registrationError, setRegistrationError] = useState(false);
  const [registrationRefresh, setRegistrationRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [editorError, setEditorError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [moreUser, setMoreUser] = useState<string | null>(null);
  const [detailTarget, setDetailTarget] = useState<AdminUser | null>(null);
  const [confirmation, setConfirmation] = useState<{ user: AdminUser; kind: "role" | "disabled" } | null>(null);
  // 搜索/筛选/分页
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // 弹窗状态
  const [pwdTarget, setPwdTarget] = useState<AdminUser | null>(null);
  const [newPwd, setNewPwd] = useState("");
  const [nameTarget, setNameTarget] = useState<AdminUser | null>(null);
  const [newName, setNewName] = useState("");
  const [delTarget, setDelTarget] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ username: "", password: "", display_name: "", role: "user" });
  const createDirty = !!(form.username || form.display_name || form.password || form.role !== "user");
  const discardCreate = () => { setCreating(false); setForm({ username: "", password: "", display_name: "", role: "user" }); };
  // 重开同一用户或继续编辑也是新草稿，旧提交只能收口它提交时的版本。
  const editorGeneration = useRef(0);
  const editDraft = (update: () => void) => {
    if (submitting.current) return;
    editorGeneration.current += 1;
    setEditorError("");
    update();
  };
  useEffect(() => { setShowPassword(false); }, [creating, pwdTarget]);
  const closeEditor = (update: () => void, dirty = false) => {
    if (submitting.current) return;
    if (dirty && !window.confirm("尚有未保存的修改，确定放弃吗？")) return;
    editDraft(update);
  };
  // 所有编辑请求共用同步锁；失败保留草稿，禁止把未知结果当成成功。
  const submit = async (action: () => Promise<unknown>) => {
    if (submitting.current) return false;
    submitting.current = true; setBusy(true); setEditorError("");
    try { await action(); return true; }
    catch (error) {
      setEditorError(error instanceof ApiError && error.status < 500
        ? error.message : "提交结果尚未确认，请刷新核对账号状态后再重试。");
      return false;
    } finally { submitting.current = false; setBusy(false); }
  };

  // 搜索框输入防抖 300ms 再触发请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  // 筛选或分页变化时请求列表；筛选变化且不在第 1 页时先回到第 1 页，避免同一渲染里
  // 用旧 page 多打一次请求（那次请求可能因网络时序覆盖正确结果，是真实竞态而非无害浪费）
  const prevFiltersRef = useRef({ debouncedQ, roleFilter, statusFilter, pageSize });
  // 操作完成时只发刷新信号，避免异步闭包把旧筛选或页码带回请求。
  const load = () => { quietRefresh.current = false; setRefresh((value) => value + 1); };
  const loadQuietly = () => { quietRefresh.current = true; setRefresh((value) => value + 1); };
  useEffect(() => {
    const prev = prevFiltersRef.current;
    const filtersChanged =
      prev.debouncedQ !== debouncedQ || prev.roleFilter !== roleFilter || prev.statusFilter !== statusFilter || prev.pageSize !== pageSize;
    prevFiltersRef.current = { debouncedQ, roleFilter, statusFilter, pageSize };
    if (filtersChanged && page !== 1) {
      setPage(1); // 下一次因 page 变化触发的 effect 才真正请求，本次跳过避免用旧 page 多打一次错误请求
      return;
    }
    let active = true;
    let reading = false;
    let hasProcessing = false;
    let pollingAllowed = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const background = quietRefresh.current && !filtersChanged;
    quietRefresh.current = false;
    setPollError(false);
    const params = new URLSearchParams({
      q: debouncedQ, role: roleFilter, status: statusFilter,
      page: String(page), page_size: String(pageSize),
    });
    const schedule = () => {
      clearTimeout(timer);
      if (active && !reading && pollingAllowed && hasProcessing && !document.hidden) {
        timer = setTimeout(() => { void read(true); }, 5000);
      }
    };
    const read = async (quiet: boolean) => {
      if (!active || reading) return;
      reading = true;
      const version = ++listVersion.current;
      if (!quiet) setLoading(true);
      setLoadError(false);
      try {
        const d = await api.get(`/api/admin/users?${params}`, { signal: AbortSignal.timeout(30_000) });
        if (!active || version !== listVersion.current) return;
        const lastPage = Math.max(1, Math.ceil((d.total || 0) / pageSize));
        if (page > lastPage) { setPage(lastPage); return; }
        const nextUsers: AdminUser[] = d.users || [];
        hasProcessing = nextUsers.some((u) => u.execution_hold?.status === "processing");
        setUsers(nextUsers);
        setTotal(d.total || 0);
        setPendingTotal(d.pending_total || 0);
        setAccountErrors((current) => {
          const next = { ...current };
          nextUsers.forEach((u) => { delete next[u.user_id]; });
          return next;
        });
      } catch {
        if (!active || version !== listVersion.current) return;
        // 查询失败不是持久收口失败；停止自动重试，等待管理员显式刷新。
        pollingAllowed = false;
        if (quiet) setPollError(true);
        else {
          setLoadError(true);
        }
      } finally {
        reading = false;
        if (active && version === listVersion.current) { setLoading(false); schedule(); }
      }
    };
    void read(background);
    document.addEventListener("visibilitychange", schedule);
    // 筛选、分页、刷新、卸载和 StrictMode 重放都使旧结果、finally 与轮询失效。
    return () => { active = false; clearTimeout(timer); document.removeEventListener("visibilitychange", schedule); };
  }, [debouncedQ, roleFilter, statusFilter, page, pageSize, refresh]);
  useEffect(() => {
    let active = true;
    setAllowReg(null); setRegistrationError(false);
    api.get("/api/admin/registration", { signal: AbortSignal.timeout(30_000) }).then((d) => { if (active) setAllowReg(d.enabled); })
      .catch(() => { if (active) setRegistrationError(true); });
    return () => { active = false; };
  }, [registrationRefresh]);

  const patch = async (u: AdminUser, body: Record<string, unknown>, okMsg: string) => {
    return submit(async () => {
      await api.patch(`/api/admin/users/${u.user_id}`, body, AbortSignal.timeout(30_000));
      toast.success(okMsg);
      load();
    });
  };

  const toggleRole = (u: AdminUser) =>
    patch(u, { role: u.role === "admin" ? "user" : "admin" }, "已更新角色");
  const changeAccount = async (u: AdminUser, body: Record<string, unknown>, message: string, retry = false) => {
    // ref 在同一事件轮次内去重，不能仅依赖下一次渲染后的 disabled。
    if (changingRef.current.has(u.user_id)) return false;
    changingRef.current.add(u.user_id);
    setChanging(new Set(changingRef.current));
    setAccountErrors((current) => { const next = { ...current }; delete next[u.user_id]; return next; });
    try {
      const response = retry
        ? await api.post(`/api/admin/users/${u.user_id}/execution-hold/retry`, body, {}, AbortSignal.timeout(30_000))
        : await api.patch(`/api/admin/users/${u.user_id}`, body, AbortSignal.timeout(30_000));
      listVersion.current += 1;
      if (response.user?.user_id === u.user_id) {
        setUsers((current) => current.map((item) => {
          if (item.user_id !== u.user_id) return item;
          const currentHold = item.execution_hold;
          const receivedHold = response.user.execution_hold;
          if ((currentHold?.generation ?? -1) > (receivedHold?.generation ?? -1)) return item;
          if (currentHold && receivedHold && currentHold.generation === receivedHold.generation
            && Date.parse(currentHold.updated_at) > Date.parse(receivedHold.updated_at)) return item;
          return response.user;
        }));
      }
      toast.success(message);
      loadQuietly();
      return true;
    } catch (error) {
      const message = error instanceof ApiError && error.status < 500
        ? error.status === 403 ? "无权管理该账号。" : "操作未完成，请点击刷新核对账号状态。"
        : "提交结果尚未确认，请点击刷新核对账号状态。";
      setAccountErrors((current) => ({ ...current, [u.user_id]: message }));
      return false;
    } finally {
      changingRef.current.delete(u.user_id);
      setChanging(new Set(changingRef.current));
    }
  };
  const toggleDisabled = (u: AdminUser) => changeAccount(u, { disabled: !u.disabled },
    u.disabled ? "账号已启用；历史任务不会自动继续。" : "账号已停用，新操作已拒绝；后台处理状态请查看账号行。",
  );
  const approve = (u: AdminUser) => changeAccount(u, { pending: false }, "已通过审批；历史任务不会自动继续。");


  const resetPwd = async () => {
    if (!pwdTarget || newPwd.length < 6) return;
    const generation = editorGeneration.current;
    if (!await patch(pwdTarget, { password: newPwd }, "已重置密码")) return;
    if (generation !== editorGeneration.current) return;
    editorGeneration.current += 1;
    setPwdTarget(null);
    setNewPwd("");
  };

  const renameUser = async () => {
    const name = newName.trim();
    if (!nameTarget || !name || name.length > 32) return;
    const generation = editorGeneration.current;
    if (!await patch(nameTarget, { display_name: name }, "已修改昵称")) return;
    if (generation !== editorGeneration.current) return;
    editorGeneration.current += 1;
    setNameTarget(null);
    setNewName("");
  };

  const doDelete = async () => {
    if (!delTarget) return;
    const id = delTarget.user_id;
    const ok = await submit(async () => {
      await api.del(`/api/admin/users/${id}`, AbortSignal.timeout(30_000));
      toast.success("已删除用户");
      load();
    });
    if (ok) setDelTarget(null);
  };

  const createUser = async () => {
    if (form.username.trim().length < 2 || form.password.length < 6) {
      setEditorError("用户名至少2位、密码至少6位");
      return;
    }
    const generation = editorGeneration.current;
    await submit(async () => {
      await api.post("/api/admin/users", form, {}, AbortSignal.timeout(30_000));
      toast.success("已创建用户");
      if (generation === editorGeneration.current) {
        editorGeneration.current += 1;
        setCreating(false);
        setForm({ username: "", password: "", display_name: "", role: "user" });
      }
      load();
    });
  };

  const setRegistration = async (enabled: boolean) => {
    const ok = await submit(async () => {
      await api.patch("/api/admin/registration", { enabled }, AbortSignal.timeout(30_000));
      setAllowReg(enabled);
      toast.success(enabled ? "已开放自助注册" : "已关闭自助注册");
    });
    if (!ok) { setAllowReg(null); setRegistrationError(true); }
  };

  return (
    <>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">用户管理</h1>
          <p className="text-sm text-muted-foreground">账号、角色与权限（仅管理员可见）</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <PageGuide page="admin" ready={!loading} />
          <Button size="sm" disabled={busy} onClick={() => editDraft(() => setCreating(true))} className="gap-1.5">
            <UserPlus className="h-4 w-4" /> 新建用户
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => { load(); setRegistrationRefresh(v => v + 1); }} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-7 py-6">
        <div className="space-y-4">
          {/* 自助注册开关 */}
          <section aria-label="自助注册设置" className="flex flex-wrap items-center gap-3 rounded-lg border border-border px-4 py-3 text-sm">
              <span className="font-medium">自助注册</span>
              <span className="text-muted-foreground">{allowReg === null ? registrationError ? "状态读取失败，请重试" : "正在读取…" : allowReg ? "已开放 · 新账号需管理员审批" : "已关闭 · 新账号由管理员创建"}</span>
              {registrationError ? <Button variant="outline" size="sm" disabled={busy} onClick={() => setRegistrationRefresh(v => v + 1)}>重试读取</Button> :
              <Button
                variant={allowReg ? "outline" : "default"}
                size="sm"
                disabled={allowReg === null || busy}
                onClick={() => setRegistration(!allowReg)}
              >
                {allowReg === null ? "读取中" : allowReg ? "关闭注册" : "开放注册"}
              </Button>
              }
          </section>

          {/* 用户列表 */}
          <Card>
            <CardHeader className="space-y-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="h-4 w-4 text-primary" /> {q || roleFilter || statusFilter ? "筛选结果" : "用户"}（共 {loading ? "…" : total}）
                {!loading && pendingTotal > 0 && (
                  <Button variant="outline" size="sm" className="text-amber-800 dark:text-amber-300" onClick={() => { setQ(""); setRoleFilter(""); setStatusFilter("pending"); }}>
                    <Clock className="mr-1 h-3.5 w-3.5" /> 全部待审批 {pendingTotal}
                  </Button>
                )}
              </CardTitle>
              <div className="flex flex-wrap gap-2">
                <div className="relative min-w-[200px] flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-8"
                    placeholder="搜索用户名/昵称…"
                    aria-label="搜索用户名或昵称"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                  />
                </div>
                <select
                  aria-label="角色筛选"
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                >
                  {ROLE_FILTER_OPTIONS.map((r) => (
                    <option key={r} value={r}>{r === "" ? "全部角色" : roleLabel(r)}</option>
                  ))}
                </select>
                <select
                  aria-label="状态筛选"
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  {STATUS_FILTER_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
                {(q || roleFilter || statusFilter) && <Button variant="outline" size="sm" onClick={() => { setQ(""); setRoleFilter(""); setStatusFilter(""); }}>清除筛选</Button>}
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {pollError && <p role="alert" className="text-sm text-red-700 dark:text-red-300">当前处理状态无法确认，自动更新已停止，请点击刷新。以下为上次确认的状态。</p>}
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : loadError ? (
                <p role="alert" className="text-sm text-red-700 dark:text-red-300">加载用户失败，请点击刷新重试。统计为上次确认的数据。</p>
              ) : users.length === 0 ? (
                <p className="text-sm text-muted-foreground">未找到匹配的用户</p>
              ) : (
                users.map((u) => {
                  const isMe = u.user_id === me?.user_id;
                  const canManage = myLevel > roleLevel(u.role); // 仅能操作低于自己的账号
                  return (
                    <div
                      key={u.user_id}
                      role="group" aria-label={`账号 ${u.username}`}
                      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border/60 px-3 py-2.5"
                    >
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-medium text-primary">
                        {(u.display_name || u.username).slice(0, 1).toUpperCase()}
                      </div>
                      <div className="min-w-[140px] flex-1 basis-[calc(100%-3rem)] sm:basis-0">
                        <div className="flex items-center gap-2">
                          <span className="break-all text-sm font-medium">{u.display_name || u.username}</span>
                          {isMe && <span className="text-[11px] text-muted-foreground">（我）</span>}
                        </div>
                        <div className="break-all text-xs text-muted-foreground">@{u.username}</div>
                      </div>
                      <Badge variant={u.role === "user" ? "outline" : "default"} className={u.role === "user" ? undefined : "text-teal-800 dark:text-teal-200"}>
                        {roleLabel(u.role)}
                      </Badge>
                      {u.pending ? (
                        <Badge variant="warning" className="text-amber-800 dark:text-amber-300"><Clock className="h-3.5 w-3.5" /> 待审批</Badge>
                      ) : u.disabled ? (
                        <Badge variant="danger" className="text-red-700 dark:text-red-300">已禁用</Badge>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-300">
                          <CheckCircle2 className="h-3.5 w-3.5" /> 正常
                        </span>
                      )}
                      <Button variant="outline" size="sm" disabled={busy} onClick={() => setDetailTarget(u)}>查看详情</Button>
                      {!canManage ? (
                        <span className="text-xs text-muted-foreground">
                          {isMe ? "当前账号" : "仅可管理低于自己角色的账号"}
                        </span>
                      ) : u.pending ? (
                        <div className="flex gap-1.5">
                          <Button size="sm" className="h-9 gap-1" disabled={busy || changing.has(u.user_id)} onClick={() => approve(u)}>
                            <UserCheck className="h-3.5 w-3.5" /> 通过
                          </Button>
                          <Button variant="outline" size="sm" className="h-9 gap-1 text-red-700 dark:text-red-300" disabled={busy || changing.has(u.user_id)}
                            onClick={() => editDraft(() => setDelTarget(u))}>
                            <Trash2 className="h-3.5 w-3.5" /> 拒绝并删除申请
                          </Button>
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          <Button variant="outline" size="sm" disabled={busy} title="修改昵称" onClick={() => editDraft(() => { setNameTarget(u); setNewName(u.display_name || ""); })}><Pencil className="mr-1 h-4 w-4" />编辑</Button>
                          <Button variant="outline" size="sm" disabled={busy} aria-expanded={moreUser === u.user_id} onClick={() => setMoreUser(moreUser === u.user_id ? null : u.user_id)}>更多操作</Button>
                        </div>
                      )}
                      {canManage && !u.pending && moreUser === u.user_id && <div className="flex w-full flex-wrap gap-2 border-t border-border pt-2">
                          {canSetRole && (
                            <Button
                              variant="outline" size="sm" disabled={busy}
                              title={u.role === "admin" ? "降为普通用户" : "升为管理员"}
                              onClick={() => editDraft(() => setConfirmation({ user: u, kind: "role" }))}
                            >
                              {u.role === "admin" ? <ShieldOff className="h-4 w-4" /> : <Shield className="h-4 w-4" />}
                              <span className="ml-1">调整角色</span>
                            </Button>
                          )}
                          <Button
                            variant="outline" size="sm"
                            title={u.disabled ? "启用账号" : "禁用账号"}
                            disabled={busy || changing.has(u.user_id)}
                            onClick={() => editDraft(() => setConfirmation({ user: u, kind: "disabled" }))}
                          >
                            {u.disabled ? <CheckCircle2 className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
                            <span className="ml-1">{u.disabled ? "启用账号" : "停用账号"}</span>
                          </Button>
                          <Button
                            variant="outline" size="sm" disabled={busy}
                            title="重置密码"
                            onClick={() => editDraft(() => { setPwdTarget(u); setNewPwd(""); })}
                          >
                            <KeyRound className="mr-1 h-4 w-4" /> 重置密码
                          </Button>
                          <Button
                            variant="outline" size="sm" disabled={busy || changing.has(u.user_id)}
                            className="text-red-700 dark:text-red-300"
                            title="删除用户"
                            onClick={() => editDraft(() => setDelTarget(u))}
                          >
                            <Trash2 className="mr-1 h-4 w-4" /> 删除用户
                          </Button>
                        </div>}
                      {changing.has(u.user_id) && <p role="status" className="w-full text-xs text-muted-foreground">正在提交账号操作…</p>}
                      {accountErrors[u.user_id] && <p role="alert" className="w-full text-sm text-red-700 dark:text-red-300">{accountErrors[u.user_id]}</p>}
                      {u.execution_hold && (
                        <div role="status" aria-live="polite" className="w-full space-y-1 text-xs text-muted-foreground">
                          <p>{u.disabled || u.pending ? "新操作已拒绝。" : "账号已启用；历史任务不会自动继续。"}</p>
                          <p>{u.execution_hold.status === "processing"
                            ? `后台处理进行中，尚有 ${u.execution_hold.pending_count} 项待确认。`
                            : u.execution_hold.status === "completed"
                              ? "后台执行处理已完成；历史任务不会自动继续。"
                              : u.execution_hold.status === "failed"
                                ? `后台处理未完成。${holdErrors[u.execution_hold.error_code || ""] || "请刷新或重试处理。"}`
                                : "后台处理状态无法确认，请刷新。"}</p>
                          {canManage && u.execution_hold.status === "failed" && u.execution_hold.retryable && (
                            <Button size="sm" variant="outline" disabled={changing.has(u.user_id)} onClick={() => changeAccount(u,
                              { operation_id: u.execution_hold!.operation_id }, "已提交处理重试，请查看后台处理状态。", true,
                            )}>重试处理</Button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </CardContent>
            <div className="border-t border-border px-4">
              <Pagination page={page} totalPages={Math.max(1, Math.ceil(total / pageSize))} total={total} pageSize={pageSize} pageSizeOptions={[10, 20, 50, 100]} onPageSizeChange={setPageSize} onChange={setPage} disabled={loading || loadError} />
            </div>
          </Card>
        </div>
      </div>

      {/* 新建用户 */}
      <Modal open={creating} onClose={() => closeEditor(discardCreate, createDirty)} title="新建用户">
        <fieldset disabled={busy} className="space-y-3">
          <label className="block space-y-1 text-sm"><span>用户名（必填，至少2位）</span>
          <Input autoComplete="off" placeholder="用户名（≥2位）" value={form.username}
            onChange={(e) => editDraft(() => setForm({ ...form, username: e.target.value }))} />
          </label><label className="block space-y-1 text-sm"><span>昵称（可选，最多32字）</span>
          <Input maxLength={32} placeholder="昵称（可选）" value={form.display_name}
            onChange={(e) => editDraft(() => setForm({ ...form, display_name: e.target.value }))} /></label>
          <label className="block space-y-1 text-sm"><span>初始密码（必填，至少6位）</span>
          <Input autoComplete="new-password" type={showPassword ? "text" : "password"} placeholder="密码（≥6位）" value={form.password}
            onChange={(e) => editDraft(() => setForm({ ...form, password: e.target.value }))} />
          </label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showPassword} onChange={e => setShowPassword(e.target.checked)} />显示密码</label>
          <p className="text-xs text-muted-foreground">创建后，请通过安全渠道将账号和初始密码告知用户；此处不会自动发送通知。</p>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">角色</span>
            <div className="flex gap-1">
              {(canSetRole ? (["user", "admin"] as const) : (["user"] as const)).map((r) => (
                <Button key={r} type="button" size="sm" aria-pressed={form.role === r} variant={form.role === r ? "default" : "outline"}
                  onClick={() => editDraft(() => setForm({ ...form, role: r }))}>
                  {roleLabel(r)}
                </Button>
              ))}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{form.role === "admin" ? "管理员可访问管理模块，并管理普通用户账号。" : "普通用户使用业务功能，不能管理其他账号。"}</p>
        </fieldset>
        {editorError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={() => closeEditor(discardCreate, createDirty)}>取消</Button>
          <Button size="sm" disabled={busy} onClick={createUser}>{busy ? "创建中…" : "创建"}</Button>
        </div>
      </Modal>

      {/* 重置密码 */}
      <Modal open={!!pwdTarget} onClose={() => closeEditor(() => setPwdTarget(null), !!newPwd)} title={`重置密码 · ${pwdTarget?.username ?? ""}`}>
        <label className="block space-y-1 text-sm"><span>新密码（至少6位）</span>
        <Input disabled={busy} autoComplete="new-password" type={showPassword ? "text" : "password"} placeholder="新密码（≥6位）" value={newPwd}
          onChange={(e) => editDraft(() => setNewPwd(e.target.value))}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && resetPwd()} /></label>
        <label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" disabled={busy} checked={showPassword} onChange={e => setShowPassword(e.target.checked)} />显示密码</label>
        <p className="mt-2 text-xs text-muted-foreground">保存后旧密码将失效，请通过安全渠道告知用户新密码。</p>
        {editorError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={() => closeEditor(() => setPwdTarget(null), !!newPwd)}>取消</Button>
          <Button size="sm" disabled={busy || newPwd.length < 6} onClick={resetPwd}>{busy ? "保存中…" : "确定"}</Button>
        </div>
      </Modal>

      {/* 修改昵称 */}
      <Modal open={!!nameTarget} onClose={() => closeEditor(() => setNameTarget(null), newName !== (nameTarget?.display_name || ""))} title={`修改昵称 · @${nameTarget?.username ?? ""}`}>
        <label className="block space-y-1 text-sm"><span>昵称（1~32字）</span>
        <Input disabled={busy} maxLength={32} placeholder="新昵称（1~32 字符）" value={newName}
          onChange={(e) => editDraft(() => setNewName(e.target.value))}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && renameUser()} /></label>
        {editorError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={() => closeEditor(() => setNameTarget(null), newName !== (nameTarget?.display_name || ""))}>取消</Button>
          <Button size="sm" disabled={busy || !newName.trim() || newName.trim().length > 32} onClick={renameUser}>{busy ? "保存中…" : "确定"}</Button>
        </div>
      </Modal>

      {/* 删除确认 */}
      <Modal open={!!delTarget} onClose={() => closeEditor(() => setDelTarget(null))} title={delTarget?.pending ? "拒绝并删除申请" : "删除用户"}>
        <p className="text-sm text-muted-foreground">
          确定{delTarget?.pending ? "拒绝申请并删除账号" : "删除用户"}「{delTarget?.display_name || delTarget?.username}」（@{delTarget?.username}）？其会话与消息将一并删除，不可撤销。
        </p>
        {editorError && <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={busy} onClick={() => closeEditor(() => setDelTarget(null))}>取消</Button>
          <Button variant="destructive" size="sm" disabled={busy} onClick={doDelete}>{busy ? "删除中…" : "确认删除"}</Button>
        </div>
      </Modal>
      <Modal open={!!confirmation} onClose={() => closeEditor(() => setConfirmation(null))} title={confirmation?.kind === "role" ? "确认调整角色" : confirmation?.user.disabled ? "确认启用账号" : "确认停用账号"}>
        {confirmation && <div className="space-y-3 text-sm">
          <p className="break-all">{confirmation.user.display_name}（@{confirmation.user.username}）</p>
          {confirmation.kind === "role" ? <><p>{roleLabel(confirmation.user.role)} → {confirmation.user.role === "admin" ? "普通用户" : "管理员"}</p><p className="text-muted-foreground">{confirmation.user.role === "admin" ? "将失去管理模块的访问和账号管理权限。" : "将获得管理模块访问权限，并可管理普通用户账号。"}</p></> : <p className="text-muted-foreground">{confirmation.user.disabled ? "恢复账号使用权限。历史任务不会自动继续。" : "将禁止账号的新操作，并在后台停止相关执行。请确认不会影响正在进行的工作。"}</p>}
          {editorError && <p role="alert" className="text-red-700 dark:text-red-300">{editorError}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" disabled={busy} onClick={() => closeEditor(() => setConfirmation(null))}>取消</Button>
            <Button disabled={busy} onClick={async () => {
              const ok = confirmation.kind === "role" ? await toggleRole(confirmation.user) : await submit(async () => {
                if (!await toggleDisabled(confirmation.user)) throw new Error("未确认");
              });
              if (ok) setConfirmation(null);
            }}>{busy ? "提交中…" : "确认"}</Button>
          </div>
        </div>}
      </Modal>
      <Modal open={!!detailTarget} onClose={() => setDetailTarget(null)} title="用户详情">
        {detailTarget && <dl className="space-y-3 break-words text-sm">
          <div><dt className="text-muted-foreground">昵称</dt><dd>{detailTarget.display_name || "未设置"}</dd></div>
          <div><dt className="text-muted-foreground">用户名</dt><dd>{detailTarget.username}</dd></div>
          <div><dt className="text-muted-foreground">用户 ID</dt><dd>{detailTarget.user_id}</dd></div>
          <div><dt className="text-muted-foreground">角色</dt><dd>{roleLabel(detailTarget.role)}</dd></div>
          <div><dt className="text-muted-foreground">状态</dt><dd>{detailTarget.pending ? "待审批" : detailTarget.disabled ? "已禁用" : "正常"}</dd></div>
          <div><dt className="text-muted-foreground">创建时间</dt><dd>{beijingTime(detailTarget.created_at)}</dd></div>
        </dl>}
        <div className="mt-4 flex justify-end"><Button variant="outline" onClick={() => setDetailTarget(null)}>关闭</Button></div>
      </Modal>
    </>
  );
}
