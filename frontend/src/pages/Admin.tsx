import { useEffect, useRef, useState } from "react";
import {
  Users, RefreshCw, Shield, ShieldOff, KeyRound, Trash2, UserPlus, Ban, CheckCircle2, UserCheck, Clock, Pencil,
  Search, ChevronLeft, ChevronRight,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
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

const PAGE_SIZE = 20;
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
  // 搜索/筛选/分页
  const [q, setQ] = useState("");
  const [searchComposing, setSearchComposing] = useState(false);
  const [debouncedQ, setDebouncedQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  // 弹窗状态
  const [pwdTarget, setPwdTarget] = useState<AdminUser | null>(null);
  const [newPwd, setNewPwd] = useState("");
  const [nameTarget, setNameTarget] = useState<AdminUser | null>(null);
  const [newName, setNewName] = useState("");
  const [delTarget, setDelTarget] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ username: "", password: "", display_name: "", role: "user" });
  // 重开同一用户或继续编辑也是新草稿，旧提交只能收口它提交时的版本。
  const editorGeneration = useRef(0);
  const [editorError, setEditorError] = useState("");
  const editDraft = (update: () => void) => {
    editorGeneration.current += 1;
    setEditorError("");
    update();
  };

  // 搜索框输入防抖 300ms 再触发请求
  useEffect(() => {
    if (searchComposing) return;
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q, searchComposing]);

  // 筛选或分页变化时请求列表；筛选变化且不在第 1 页时先回到第 1 页，避免同一渲染里
  // 用旧 page 多打一次请求（那次请求可能因网络时序覆盖正确结果，是真实竞态而非无害浪费）
  const prevFiltersRef = useRef({ debouncedQ, roleFilter, statusFilter });
  // 操作完成时只发刷新信号，避免异步闭包把旧筛选或页码带回请求。
  const load = () => { quietRefresh.current = false; setRefresh((value) => value + 1); };
  const loadQuietly = () => { quietRefresh.current = true; setRefresh((value) => value + 1); };
  useEffect(() => {
    const prev = prevFiltersRef.current;
    const filtersChanged =
      prev.debouncedQ !== debouncedQ || prev.roleFilter !== roleFilter || prev.statusFilter !== statusFilter;
    prevFiltersRef.current = { debouncedQ, roleFilter, statusFilter };
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
      page: String(page), page_size: String(PAGE_SIZE),
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
        const d = await api.get(`/api/admin/users?${params}`);
        if (!active || version !== listVersion.current) return;
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
          setUsers([]);
          setTotal(0);
          setPendingTotal(0);
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
  }, [debouncedQ, roleFilter, statusFilter, page, refresh]);
  useEffect(() => {
    api.get("/api/admin/registration").then((d) => setAllowReg(d.enabled)).catch(() => {});
  }, []);

  const patch = async (u: AdminUser, body: Record<string, unknown>, okMsg: string) => {
    try {
      await api.patch(`/api/admin/users/${u.user_id}`, body);
      toast.success(okMsg);
      load();
      return true;
    } catch (e: any) {
      toast.error(e.message || "操作失败");
      return false;
    }
  };

  const toggleRole = (u: AdminUser) =>
    patch(u, { role: u.role === "admin" ? "user" : "admin" }, "已更新角色");
  const changeAccount = async (u: AdminUser, body: Record<string, unknown>, message: string, retry = false) => {
    // ref 在同一事件轮次内去重，不能仅依赖下一次渲染后的 disabled。
    if (changingRef.current.has(u.user_id)) return;
    changingRef.current.add(u.user_id);
    setChanging(new Set(changingRef.current));
    setAccountErrors((current) => { const next = { ...current }; delete next[u.user_id]; return next; });
    try {
      const response = retry
        ? await api.post(`/api/admin/users/${u.user_id}/execution-hold/retry`, body)
        : await api.patch(`/api/admin/users/${u.user_id}`, body);
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
    } catch (error) {
      const message = error instanceof ApiError && error.status < 500
        ? error.status === 403 ? "无权管理该账号。" : "操作未完成，请点击刷新核对账号状态。"
        : "提交结果尚未确认，请点击刷新核对账号状态。";
      setAccountErrors((current) => ({ ...current, [u.user_id]: message }));
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
    const saved = await patch(pwdTarget, { password: newPwd }, "已重置密码");
    if (generation !== editorGeneration.current) return;
    if (!saved) { setEditorError("重置密码未收到成功确认，已保留输入，请核对后重试。"); return; }
    editorGeneration.current += 1;
    setPwdTarget(null);
    setNewPwd("");
  };

  const renameUser = async () => {
    const name = newName.trim();
    if (!nameTarget || !name || name.length > 32) return;
    const generation = editorGeneration.current;
    const saved = await patch(nameTarget, { display_name: name }, "已修改昵称");
    if (generation !== editorGeneration.current) return;
    if (!saved) { setEditorError("修改昵称未收到成功确认，已保留输入，请核对后重试。"); return; }
    editorGeneration.current += 1;
    setNameTarget(null);
    setNewName("");
  };

  const doDelete = async () => {
    if (!delTarget) return;
    const id = delTarget.user_id;
    setDelTarget(null);
    try {
      await api.del(`/api/admin/users/${id}`);
      toast.success("已删除用户");
      load();
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };

  const createUser = async () => {
    if (form.username.trim().length < 2 || form.password.length < 6) {
      toast.error("用户名至少2位、密码至少6位");
      return;
    }
    const generation = editorGeneration.current;
    try {
      await api.post("/api/admin/users", form);
      toast.success("已创建用户");
      if (generation === editorGeneration.current) {
        editorGeneration.current += 1;
        setCreating(false);
        setForm({ username: "", password: "", display_name: "", role: "user" });
      }
      load();
    } catch (e: any) {
      toast.error(e.message || "创建失败");
    }
  };

  const setRegistration = async (enabled: boolean) => {
    try {
      await api.patch("/api/admin/registration", { enabled });
      setAllowReg(enabled);
      toast.success(enabled ? "已开放自助注册" : "已关闭自助注册");
    } catch (e: any) {
      toast.error(e.message || "操作失败");
    }
  };

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">用户管理</h1>
          <p className="text-sm text-muted-foreground">账号、角色与权限（仅管理员可见）</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => editDraft(() => setCreating(true))} className="gap-1.5">
            <UserPlus className="h-4 w-4" /> 新建用户
          </Button>
          <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-7 py-6">
        <div className="mx-auto max-w-4xl space-y-5">
          {/* 自助注册开关 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">自助注册</CardTitle>
            </CardHeader>
            <CardContent className="flex items-center justify-between">
              <div className="text-sm text-muted-foreground">
                关闭后，登录页将无法自助注册，新账号只能由管理员在此创建。
              </div>
              <Button
                variant={allowReg ? "outline" : "default"}
                size="sm"
                disabled={allowReg === null}
                onClick={() => setRegistration(!allowReg)}
              >
                {allowReg ? "关闭注册" : "开放注册"}
              </Button>
            </CardContent>
          </Card>

          {/* 用户列表 */}
          <Card>
            <CardHeader className="space-y-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="h-4 w-4 text-primary" /> 用户（共 {loading ? "…" : total}）
                {!loading && pendingTotal > 0 && (
                  <Badge variant="warning">
                    <Clock className="h-3.5 w-3.5" /> {pendingTotal} 待审批
                  </Badge>
                )}
              </CardTitle>
              <div className="flex flex-wrap gap-2">
                <div className="min-w-[200px] flex-1">
                  <label htmlFor="admin-user-search" className="mb-1 block text-xs text-muted-foreground">搜索用户</label>
                  <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="admin-user-search"
                    className="pl-8"
                    placeholder="搜索用户名/昵称…"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    onCompositionStart={() => setSearchComposing(true)}
                    onCompositionEnd={(e) => { setQ(e.currentTarget.value); setSearchComposing(false); }}
                  />
                  </div>
                </div>
                <label className="text-xs text-muted-foreground">筛选角色
                <select
                  className="mt-1 block h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                >
                  {ROLE_FILTER_OPTIONS.map((r) => (
                    <option key={r} value={r}>{r === "" ? "全部角色" : roleLabel(r)}</option>
                  ))}
                </select>
                </label>
                <label className="text-xs text-muted-foreground">筛选状态
                <select
                  className="mt-1 block h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  {STATUS_FILTER_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
                </label>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {pollError && <p role="alert" className="text-sm text-destructive">当前处理状态无法确认，自动更新已停止，请点击刷新。以下为上次确认的状态。</p>}
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : loadError ? (
                <p role="alert" className="text-sm text-destructive">加载用户失败，请点击刷新重试。</p>
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
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium">{u.display_name || u.username}</span>
                          {isMe && <span className="text-[11px] text-muted-foreground">（我）</span>}
                        </div>
                        <div className="truncate text-[11px] text-muted-foreground">@{u.username}</div>
                      </div>
                      <Badge variant={u.role === "user" ? "outline" : "default"}>
                        {roleLabel(u.role)}
                      </Badge>
                      {u.pending ? (
                        <Badge variant="warning"><Clock className="h-3.5 w-3.5" /> 待审批</Badge>
                      ) : u.disabled ? (
                        <Badge variant="danger">已禁用</Badge>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400">
                          <CheckCircle2 className="h-3.5 w-3.5" /> 正常
                        </span>
                      )}
                      {!canManage ? (
                        <span className="text-[11px] text-muted-foreground/60">
                          {isMe ? "（我）" : "无权管理"}
                        </span>
                      ) : u.pending ? (
                        <div className="flex gap-1.5">
                          <Button size="sm" className="h-7 gap-1" disabled={changing.has(u.user_id)} onClick={() => approve(u)}>
                            <UserCheck className="h-3.5 w-3.5" /> 通过
                          </Button>
                          <Button variant="outline" size="sm" className="h-7 gap-1 text-destructive"
                            onClick={() => setDelTarget(u)}>
                            <Trash2 className="h-3.5 w-3.5" /> 拒绝
                          </Button>
                        </div>
                      ) : (
                        <div className="flex gap-1">
                          {canSetRole && (
                            <Button
                              variant="ghost" size="icon" className="h-7 w-7"
                              title={u.role === "admin" ? "降为普通用户" : "升为管理员"}
                              onClick={() => toggleRole(u)}
                            >
                              {u.role === "admin" ? <ShieldOff className="h-4 w-4" /> : <Shield className="h-4 w-4" />}
                            </Button>
                          )}
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            title={u.disabled ? "启用账号" : "禁用账号"}
                            disabled={changing.has(u.user_id)}
                            onClick={() => toggleDisabled(u)}
                          >
                            {u.disabled ? <CheckCircle2 className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            title="修改昵称"
                            onClick={() => editDraft(() => { setNameTarget(u); setNewName(u.display_name || ""); })}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            title="重置密码"
                            onClick={() => editDraft(() => { setPwdTarget(u); setNewPwd(""); })}
                          >
                            <KeyRound className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost" size="icon"
                            className="h-7 w-7 text-muted-foreground hover:text-destructive"
                            title="删除用户"
                            onClick={() => setDelTarget(u)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      )}
                      {changing.has(u.user_id) && <p role="status" className="w-full text-xs text-muted-foreground">正在提交账号操作…</p>}
                      {accountErrors[u.user_id] && <p role="alert" className="w-full text-sm text-destructive">{accountErrors[u.user_id]}</p>}
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
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-center gap-3 border-t border-border/60 px-4 py-3 text-sm text-muted-foreground">
                <Button
                  variant="outline" size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="h-4 w-4" /> 上一页
                </Button>
                <span>{loading ? "加载分页信息…" : `第 ${page} / ${Math.max(1, Math.ceil(total / PAGE_SIZE))} 页`}</span>
                <Button
                  variant="outline" size="sm"
                  disabled={page >= Math.max(1, Math.ceil(total / PAGE_SIZE))}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页 <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            )}
          </Card>
        </div>
      </div>

      {/* 新建用户 */}
      <Modal open={creating} onClose={() => editDraft(() => setCreating(false))} title="新建用户">
        <div className="space-y-3">
          <div><label htmlFor="admin-create-username" className="mb-1 block text-sm">用户名</label>
          <Input id="admin-create-username" placeholder="用户名（≥2位）" value={form.username}
            onChange={(e) => editDraft(() => setForm({ ...form, username: e.target.value }))} />
          </div>
          <div><label htmlFor="admin-create-name" className="mb-1 block text-sm">昵称（可选）</label>
          <Input id="admin-create-name" placeholder="昵称（可选）" value={form.display_name}
            onChange={(e) => editDraft(() => setForm({ ...form, display_name: e.target.value }))} />
          </div>
          <div><label htmlFor="admin-create-password" className="mb-1 block text-sm">初始密码</label>
          <Input id="admin-create-password" type="password" placeholder="密码（≥6位）" value={form.password}
            onChange={(e) => editDraft(() => setForm({ ...form, password: e.target.value }))} />
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">角色</span>
            <div className="flex gap-1">
              {(canSetRole ? (["user", "admin"] as const) : (["user"] as const)).map((r) => (
                <Button key={r} type="button" size="sm" variant={form.role === r ? "default" : "outline"}
                  onClick={() => editDraft(() => setForm({ ...form, role: r }))}>
                  {roleLabel(r)}
                </Button>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => editDraft(() => setCreating(false))}>取消</Button>
          <Button size="sm" onClick={createUser}>创建</Button>
        </div>
      </Modal>

      {/* 重置密码 */}
      <Modal open={!!pwdTarget} onClose={() => editDraft(() => setPwdTarget(null))} title={`重置密码 · ${pwdTarget?.username ?? ""}`}>
        <label htmlFor="admin-new-password" className="mb-1 block text-sm">新密码</label>
        <Input id="admin-new-password" type="password" placeholder="新密码（≥6位）" value={newPwd}
          onChange={(e) => editDraft(() => setNewPwd(e.target.value))}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229 && resetPwd()} />
        {editorError && <p role="alert" className="mt-2 text-sm text-destructive">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => editDraft(() => setPwdTarget(null))}>取消</Button>
          <Button size="sm" disabled={newPwd.length < 6} onClick={resetPwd}>确定</Button>
        </div>
      </Modal>

      {/* 修改昵称 */}
      <Modal open={!!nameTarget} onClose={() => editDraft(() => setNameTarget(null))} title={`修改昵称 · @${nameTarget?.username ?? ""}`}>
        <label htmlFor="admin-new-name" className="mb-1 block text-sm">新昵称</label>
        <Input id="admin-new-name" placeholder="新昵称（1~32 字符）" value={newName}
          onChange={(e) => editDraft(() => setNewName(e.target.value))}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229 && renameUser()} />
        {editorError && <p role="alert" className="mt-2 text-sm text-destructive">{editorError}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => editDraft(() => setNameTarget(null))}>取消</Button>
          <Button size="sm" disabled={!newName.trim() || newName.trim().length > 32} onClick={renameUser}>确定</Button>
        </div>
      </Modal>

      {/* 删除确认 */}
      <Modal open={!!delTarget} onClose={() => setDelTarget(null)} title="删除用户">
        <p className="text-sm text-muted-foreground">
          确定删除用户「{delTarget?.display_name || delTarget?.username}」？其会话与消息将一并删除，不可撤销。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setDelTarget(null)}>取消</Button>
          <Button variant="destructive" size="sm" onClick={doDelete}>删除</Button>
        </div>
      </Modal>
    </>
  );
}
