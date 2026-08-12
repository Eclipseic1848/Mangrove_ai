import { useEffect, useState } from "react";
import { Brain, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Markdown } from "@/components/Markdown";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";
import { formatRelativeTime } from "@/lib/utils";

interface PersonalMemory {
  id: number;
  text: string;
  created_at: string;
}

export function Memory() {
  const { user } = useAuth();
  const isAdmin = isAdminish(user?.role);
  const [pref, setPref] = useState("");
  const [personal, setPersonal] = useState<PersonalMemory[]>([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [myText, setMyText] = useState("");
  const [mySaving, setMySaving] = useState(false);

  const load = () => {
    setLoading(true);
    api
      .get("/api/memory")
      .then((d) => {
        setPref(d.preferences || "");
        setPersonal(d.personal || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const add = async () => {
    const t = text.trim();
    if (!t) return;
    setSaving(true);
    try {
      await api.post("/api/memory", { text: t });
      toast.success("已记住该偏好");
      setText("");
      load();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const addMine = async () => {
    const t = myText.trim();
    if (!t) return;
    setMySaving(true);
    try {
      await api.post("/api/memory/self", { text: t });
      toast.success("已记住");
      setMyText("");
      load();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setMySaving(false);
    }
  };

  const deleteMine = async (id: number) => {
    try {
      await api.del(`/api/memory/self/${id}`);
      setPersonal((items) => items.filter((it) => it.id !== id));
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">记忆</h1>
          <p className="text-sm text-muted-foreground">跨会话的偏好，会注入意图理解</p>
        </div>
        <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
          <RefreshCw className="h-4 w-4" /> 刷新
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-7 py-6">
        <div className="mx-auto max-w-3xl space-y-5">
          {/* 我的记忆：每个用户自己的偏好，只对自己发起的任务生效 */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">我的记忆</CardTitle>
              <p className="text-xs text-muted-foreground">只对你自己发起的任务生效，不影响其他人</p>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <Input
                  value={myText}
                  onChange={(e) => setMyText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && addMine()}
                  placeholder="例如：报告优先用表格呈现"
                  disabled={mySaving}
                />
                <Button onClick={addMine} disabled={mySaving || !myText.trim()} className="shrink-0 gap-1.5">
                  <Plus className="h-4 w-4" /> 记住
                </Button>
              </div>
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : personal.length ? (
                <div className="space-y-1.5">
                  {personal.map((it) => (
                    <div
                      key={it.id}
                      className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-3 py-2 text-sm"
                    >
                      <span className="min-w-0 flex-1 truncate">{it.text}</span>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        {formatRelativeTime(it.created_at)}
                      </span>
                      <Button
                        variant="ghost" size="sm" className="h-7 shrink-0 gap-1 px-2"
                        onClick={() => deleteMine(it.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="py-4 text-center text-sm text-muted-foreground">还没有个人记忆</p>
              )}
            </CardContent>
          </Card>

          {/* 全局记忆：管理员维护的全局规范，对所有人生效 */}
          {isAdmin && (
            <Card>
              <CardContent className="flex gap-2 p-4">
                <Input
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && add()}
                  placeholder="例如：报告优先用表格呈现；默认采集汽车之家口碑"
                  disabled={saving}
                />
                <Button onClick={add} disabled={saving || !text.trim()} className="shrink-0 gap-1.5">
                  <Plus className="h-4 w-4" /> 记住
                </Button>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">全局记忆</CardTitle>
              <p className="text-xs text-muted-foreground">
                {isAdmin ? "你维护的全局规范，对所有人生效" : "管理员维护的全局规范，对所有人生效"}
              </p>
            </CardHeader>
            <CardContent>
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : pref ? (
                <Markdown>{pref}</Markdown>
              ) : (
                <div className="py-12 text-center">
                  <Brain className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    还没有全局记忆。{isAdmin ? "添加一条偏好，智能体会在后续任务中遵循。" : ""}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
