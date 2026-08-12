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
import { api } from "@/lib/api";
import { useAuth, roleLevel, roleLabel, ROLE_LEVEL } from "@/lib/auth";

interface AdminUser {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
  disabled: number;
  pending: number;
  created_at: string;
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
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  // 搜索/筛选/分页
  const [q, setQ] = useState("");
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

  // 搜索框输入防抖 300ms 再触发请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  // 筛选或分页变化时请求列表；筛选变化且不在第 1 页时先回到第 1 页，避免同一渲染里
  // 用旧 page 多打一次请求（那次请求可能因网络时序覆盖正确结果，是真实竞态而非无害浪费）
  const prevFiltersRef = useRef({ debouncedQ, roleFilter, statusFilter });
  const load = () => {
    setLoading(true);
    const params = new URLSearchParams({
      q: debouncedQ, role: roleFilter, status: statusFilter,
      page: String(page), page_size: String(PAGE_SIZE),
    });
    api.get(`/api/admin/users?${params}`)
      .then((d) => {
        setUsers(d.users || []);
        setTotal(d.total || 0);
        setPendingTotal(d.pending_total || 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    const prev = prevFiltersRef.current;
    const filtersChanged =
      prev.debouncedQ !== debouncedQ || prev.roleFilter !== roleFilter || prev.statusFilter !== statusFilter;
    prevFiltersRef.current = { debouncedQ, roleFilter, statusFilter };
    if (filtersChanged && page !== 1) {
      setPage(1); // 下一次因 page 变化触发的 effect 才真正请求，本次跳过避免用旧 page 多打一次错误请求
      return;
    }
    load();
  }, [debouncedQ, roleFilter, statusFilter, page]);
  useEffect(() => {
    api.get("/api/admin/registration").then((d) => setAllowReg(d.enabled)).catch(() => {});
  }, []);

  const patch = async (u: AdminUser, body: Record<string, unknown>, okMsg: string) => {
    try {
      await api.patch(`/api/admin/users/${u.user_id}`, body);
      toast.success(okMsg);
      load();
    } catch (e: any) {
      toast.error(e.message || "操作失败");
    }
  };

  const toggleRole = (u: AdminUser) =>
    patch(u, { role: u.role === "admin" ? "user" : "admin" }, "已更新角色");
  const toggleDisabled = (u: AdminUser) =>
    patch(u, { disabled: !u.disabled }, u.disabled ? "已启用账号" : "已禁用账号");
  const approve = (u: AdminUser) => patch(u, { pending: false }, "已通过审批");

  const resetPwd = async () => {
    if (!pwdTarget || newPwd.length < 6) return;
    await patch(pwdTarget, { password: newPwd }, "已重置密码");
    setPwdTarget(null);
    setNewPwd("");
  };

  const renameUser = async () => {
    const name = newName.trim();
    if (!nameTarget || !name || name.length > 32) return;
    await patch(nameTarget, { display_name: name }, "已修改昵称");
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
    try {
      await api.post("/api/admin/users", form);
      toast.success("已创建用户");
      setCreating(false);
      setForm({ username: "", password: "", display_name: "", role: "user" });
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
          <Button variant="outline" size="sm" onClick={() => setCreating(true)} className="gap-1.5">
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
                <Users className="h-4 w-4 text-primary" /> 用户（共 {total}）
                {pendingTotal > 0 && (
                  <Badge variant="warning">
                    <Clock className="h-3.5 w-3.5" /> {pendingTotal} 待审批
                  </Badge>
                )}
              </CardTitle>
              <div className="flex flex-wrap gap-2">
                <div className="relative min-w-[200px] flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-8"
                    placeholder="搜索用户名/昵称…"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                  />
                </div>
                <select
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                >
                  {ROLE_FILTER_OPTIONS.map((r) => (
                    <option key={r} value={r}>{r === "" ? "全部角色" : roleLabel(r)}</option>
                  ))}
                </select>
                <select
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  {STATUS_FILTER_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : users.length === 0 ? (
                <p className="text-sm text-muted-foreground">未找到匹配的用户</p>
              ) : (
                users.map((u) => {
                  const isMe = u.user_id === me?.user_id;
                  const canManage = myLevel > roleLevel(u.role); // 仅能操作低于自己的账号
                  return (
                    <div
                      key={u.user_id}
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
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-500">
                          <CheckCircle2 className="h-3.5 w-3.5" /> 正常
                        </span>
                      )}
                      {!canManage ? (
                        <span className="text-[11px] text-muted-foreground/60">
                          {isMe ? "（我）" : "无权管理"}
                        </span>
                      ) : u.pending ? (
                        <div className="flex gap-1.5">
                          <Button size="sm" className="h-7 gap-1" onClick={() => approve(u)}>
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
                            onClick={() => toggleDisabled(u)}
                          >
                            {u.disabled ? <CheckCircle2 className="h-4 w-4" /> : <Ban className="h-4 w-4" />}
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            title="修改昵称"
                            onClick={() => { setNameTarget(u); setNewName(u.display_name || ""); }}
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            title="重置密码"
                            onClick={() => { setPwdTarget(u); setNewPwd(""); }}
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
                <span>第 {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页</span>
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
      <Modal open={creating} onClose={() => setCreating(false)} title="新建用户">
        <div className="space-y-3">
          <Input placeholder="用户名（≥2位）" value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <Input placeholder="昵称（可选）" value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <Input type="password" placeholder="密码（≥6位）" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">角色</span>
            <div className="flex gap-1">
              {(canSetRole ? (["user", "admin"] as const) : (["user"] as const)).map((r) => (
                <Button key={r} type="button" size="sm" variant={form.role === r ? "default" : "outline"}
                  onClick={() => setForm({ ...form, role: r })}>
                  {roleLabel(r)}
                </Button>
              ))}
            </div>
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setCreating(false)}>取消</Button>
          <Button size="sm" onClick={createUser}>创建</Button>
        </div>
      </Modal>

      {/* 重置密码 */}
      <Modal open={!!pwdTarget} onClose={() => setPwdTarget(null)} title={`重置密码 · ${pwdTarget?.username ?? ""}`}>
        <Input type="password" placeholder="新密码（≥6位）" value={newPwd}
          onChange={(e) => setNewPwd(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && resetPwd()} />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setPwdTarget(null)}>取消</Button>
          <Button size="sm" disabled={newPwd.length < 6} onClick={resetPwd}>确定</Button>
        </div>
      </Modal>

      {/* 修改昵称 */}
      <Modal open={!!nameTarget} onClose={() => setNameTarget(null)} title={`修改昵称 · @${nameTarget?.username ?? ""}`}>
        <Input placeholder="新昵称（1~32 字符）" value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && renameUser()} />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setNameTarget(null)}>取消</Button>
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
