/**
 * 数据准备页面（Phase 2 Task 11）：上传 → 预览 → 执行 → 下载的最小闭环。
 *
 * 独立路由 /data-prep，避免侵入 743 行的 Chat.tsx。用原生 input[type=file]
 * 上传（无新依赖）；复用 lib/api 鉴权与 downloadFile。
 */
import { useEffect, useState } from "react";
import { AlertCircle } from "lucide-react";
import type { UploadItem, DataTaskPreview, DataTask, DatasetManifest } from "@/types/dataPrep";
import {
  uploadFile,
  previewTask,
  createTask,
  getManifest,
  listTasks,
  rerunTask,
  listDbConnections,
  createDbConnection,
  deleteDbConnection,
  testDbConnection,
  getDbSchema,
  type DataTaskSource,
  type DbConnection,
} from "@/lib/dataPrepApi";
import { downloadFile } from "@/lib/api";
import { productText } from "@/lib/productText";

const STATUS_LABEL: Record<string, string> = {
  SUCCEEDED: "✅ 成功",
  SUCCEEDED_WITH_WARNINGS: "⚠️ 带告警成功",
  FAILED: "❌ 失败",
  RUNNING: "⏳ 运行中",
};

export function StructuredDataPrepPage() {
  const [sourceType, setSourceType] = useState<"upload_file" | "http_api" | "database">("upload_file");
  const [httpUrl, setHttpUrl] = useState("");
  const [pageParam, setPageParam] = useState("page");
  const [perPage, setPerPage] = useState(100);
  const [maxPages, setMaxPages] = useState(10);
  const [upload, setUpload] = useState<UploadItem | null>(null);
  const [preview, setPreview] = useState<DataTaskPreview | null>(null);
  const [task, setTask] = useState<DataTask | null>(null);
  const [manifest, setManifest] = useState<DatasetManifest | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // database 源状态
  const [dbConnections, setDbConnections] = useState<DbConnection[]>([]);
  const [selectedDbConnId, setSelectedDbConnId] = useState("");
  const [dbSchema, setDbSchema] = useState<any>(null);
  const [selectedTable, setSelectedTable] = useState("");
  const [selectedFields, setSelectedFields] = useState<string[]>([]);
  const [dbWatermarkField, setDbWatermarkField] = useState("");
  const [dbTimeField, setDbTimeField] = useState("");
  const [dbTimeStart, setDbTimeStart] = useState("");
  const [dbTimeEnd, setDbTimeEnd] = useState("");
  const [showDbForm, setShowDbForm] = useState(false);
  const [dbDraft, setDbDraft] = useState({
    name: "", dialect: "sqlite", host: "", port: 0,
    database_name: "", username: "", password: "", sqlite_relpath: "",
  });

  useEffect(() => {
    listTasks()
      .then(async (tasks) => {
        const latest = tasks[0];
        if (!latest) return;
        setTask(latest);
        if (latest.manifest_path) setManifest(await getManifest(latest.task_id));
      })
      .catch((e: any) => setError(e.message || "历史任务加载失败"));
  }, []);

  function currentSource(): DataTaskSource | null {
    if (sourceType === "upload_file") {
      return upload ? { source_type: "upload_file", upload_id: upload.upload_id } : null;
    }
    if (sourceType === "http_api" && httpUrl.trim()) {
      return {
        source_type: "http_api",
        url: httpUrl.trim(),
        pagination: {
          strategy: "page",
          options: {
            page_param: pageParam,
            per_page_param: "per_page",
            per_page: perPage,
            max_pages: maxPages,
          },
        },
      };
    }
    if (sourceType === "database" && selectedDbConnId && selectedTable) {
      return {
        source_type: "database",
        connection_id: selectedDbConnId,
        table: selectedTable,
        fields: selectedFields.length ? selectedFields : undefined,
        incremental: dbWatermarkField
          ? { strategy: "watermark", cursor_field: dbWatermarkField }
          : undefined,
        time_range: dbTimeField && dbTimeStart && dbTimeEnd
          ? { field: dbTimeField, start: dbTimeStart, end: dbTimeEnd }
          : undefined,
      };
    }
    return null;
  }

  function reset() {
    setUpload(null);
    setPreview(null);
    setTask(null);
    setManifest(null);
    setError(null);
  }

  async function onFile(file: File) {
    reset();
    setBusy("upload");
    try {
      setUpload(await uploadFile(file));
    } catch (e: any) {
      setError(e.message || "上传失败");
    } finally {
      setBusy(null);
    }
  }

  async function onPreview() {
    const source = currentSource();
    if (!source) return;
    setBusy("preview");
    setError(null);
    try {
      setPreview(await previewTask(source));
    } catch (e: any) {
      setError(e.message || "预览失败");
    } finally {
      setBusy(null);
    }
  }

  // 数据库源交互
  async function loadConnections() {
    try {
      setDbConnections(await listDbConnections());
    } catch (e: any) {
      setError(e.message || "加载连接列表失败");
    }
  }

  async function onSelectConnection(id: string) {
    setSelectedDbConnId(id);
    setSelectedTable("");
    setSelectedFields([]);
    setDbWatermarkField("");
    if (!id) return;
    try {
      const s = await getDbSchema(id);
      setDbSchema(s);
    } catch (e: any) {
      setError(e.message || "加载 Schema 失败");
    }
  }

  async function onTestDbDraft() {
    setBusy("db-test");
    setError(null);
    try {
      const result: any = await testDbConnection(dbDraft);
      if (!result.reachable) throw new Error(result.message || "连接失败");
    } catch (e: any) {
      setError(e.message || "连接测试失败");
    } finally {
      setBusy(null);
    }
  }

  async function onSaveDbDraft() {
    setBusy("db-save");
    setError(null);
    try {
      const created = await createDbConnection(dbDraft);
      await loadConnections();
      setShowDbForm(false);
      await onSelectConnection(created.connection_id);
    } catch (e: any) {
      setError(e.message || "保存连接失败");
    } finally {
      setBusy(null);
    }
  }

  async function onDeleteDbConnection() {
    if (!selectedDbConnId) return;
    setBusy("db-delete");
    try {
      await deleteDbConnection(selectedDbConnId);
      setSelectedDbConnId("");
      setDbSchema(null);
      await loadConnections();
    } catch (e: any) {
      setError(e.message || "删除连接失败");
    } finally {
      setBusy(null);
    }
  }

  async function onCreate() {
    const source = currentSource();
    if (!source) return;
    setBusy("create");
    setError(null);
    try {
      const t = await createTask(source, { outputs: ["jsonl", "parquet"] });
      setTask(t);
      if (t.manifest_path) setManifest(await getManifest(t.task_id));
    } catch (e: any) {
      setError(e.message || "任务执行失败");
    } finally {
      setBusy(null);
    }
  }

  async function onRerun() {
    if (!task) return;
    setBusy("rerun");
    setError(null);
    try {
      const t = await rerunTask(task.task_id);
      setTask(t);
      if (t.manifest_path) setManifest(await getManifest(t.task_id));
    } catch (e: any) {
      setError(e.message || "复跑失败");
    } finally {
      setBusy(null);
    }
  }

  const disabled = busy !== null;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-2xl font-bold">数据准备</h1>
        <p className="text-sm text-muted-foreground">
          上传文件 → 预览 Schema 与样本 → 执行清洗 → 下载干净数据
        </p>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* 1. 数据源 */}
      <section className="rounded border p-4">
        <h2 className="mb-2 font-semibold">1. 数据源</h2>
        <div className="mb-3 flex gap-2 text-sm">
          <button
            onClick={() => { setSourceType("upload_file"); reset(); }}
            className={`rounded px-3 py-1 ${sourceType === "upload_file" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
          >
            上传文件
          </button>
          <button
            onClick={() => { setSourceType("http_api"); reset(); }}
            className={`rounded px-3 py-1 ${sourceType === "http_api" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
          >
            HTTP API
          </button>
          <button
            onClick={() => { setSourceType("database"); reset(); loadConnections(); }}
            className={`rounded px-3 py-1 ${sourceType === "database" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
          >
            数据库
          </button>
        </div>
        {sourceType === "upload_file" ? (
          <>
            <input
              type="file"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onFile(f);
              }}
              disabled={busy === "upload"}
              className="block w-full text-sm"
            />
            {upload && (
              <div className="mt-2 text-sm text-muted-foreground">
                ✅ {upload.original_name}（{upload.size_bytes} 字节，{upload.media_type}）
              </div>
            )}
          </>
        ) : sourceType === "http_api" ? (
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <label className="sm:col-span-2">
              <span className="mb-1 block text-xs text-muted-foreground">API URL</span>
              <input
                aria-label="API URL"
                value={httpUrl}
                onChange={(e) => setHttpUrl(e.target.value)}
                placeholder="https://api.example.com/items"
                className="w-full rounded border bg-background px-3 py-2"
              />
            </label>
            <label>
              <span className="mb-1 block text-xs text-muted-foreground">页码参数</span>
              <input aria-label="页码参数" value={pageParam} onChange={(e) => setPageParam(e.target.value)} className="w-full rounded border bg-background px-3 py-2" />
            </label>
            <label>
              <span className="mb-1 block text-xs text-muted-foreground">每页条数</span>
              <input aria-label="每页条数" type="number" min={1} value={perPage} onChange={(e) => setPerPage(Number(e.target.value))} className="w-full rounded border bg-background px-3 py-2" />
            </label>
            <label>
              <span className="mb-1 block text-xs text-muted-foreground">最大页数</span>
              <input aria-label="最大页数" type="number" min={1} value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} className="w-full rounded border bg-background px-3 py-2" />
            </label>
          </div>
        ) : (
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <label className="sm:col-span-2">
              <span className="mb-1 block text-xs text-muted-foreground">数据库连接</span>
              <select
                aria-label="数据库连接"
                value={selectedDbConnId}
                onChange={(e) => onSelectConnection(e.target.value)}
                className="w-full rounded border bg-background px-3 py-2"
              >
                <option value="">-- 选择已有连接 --</option>
                {dbConnections.map((c) => (
                  <option key={c.connection_id} value={c.connection_id}>
                    {c.name} ({c.dialect})
                  </option>
                ))}
              </select>
            </label>
            <div className="sm:col-span-2 flex gap-2">
              <button type="button" onClick={() => setShowDbForm(!showDbForm)} className="rounded border px-3 py-1">
                {showDbForm ? "取消新建" : "新建连接"}
              </button>
              {selectedDbConnId && (
                <button type="button" onClick={onDeleteDbConnection} disabled={disabled} className="rounded border border-red-300 px-3 py-1 text-red-700">
                  删除连接
                </button>
              )}
            </div>
            {showDbForm && (
              <div className="sm:col-span-2 grid gap-2 rounded border p-3 sm:grid-cols-2">
                <input aria-label="连接名称" placeholder="连接名称" value={dbDraft.name} onChange={(e) => setDbDraft({ ...dbDraft, name: e.target.value })} className="rounded border bg-background px-2 py-1" />
                <select aria-label="数据库类型" value={dbDraft.dialect} onChange={(e) => setDbDraft({ ...dbDraft, dialect: e.target.value })} className="rounded border bg-background px-2 py-1">
                  <option value="sqlite">SQLite</option><option value="mysql">MySQL</option><option value="postgresql">PostgreSQL</option>
                </select>
                {dbDraft.dialect === "sqlite" ? (
                  <input aria-label="SQLite 相对路径" placeholder="例如 orders.db" value={dbDraft.sqlite_relpath} onChange={(e) => setDbDraft({ ...dbDraft, sqlite_relpath: e.target.value })} className="sm:col-span-2 rounded border bg-background px-2 py-1" />
                ) : (
                  <>
                    <input aria-label="数据库主机" placeholder="主机" value={dbDraft.host} onChange={(e) => setDbDraft({ ...dbDraft, host: e.target.value })} className="rounded border bg-background px-2 py-1" />
                    <input aria-label="数据库端口" type="number" placeholder="端口" value={dbDraft.port || ""} onChange={(e) => setDbDraft({ ...dbDraft, port: Number(e.target.value) })} className="rounded border bg-background px-2 py-1" />
                    <input aria-label="数据库名" placeholder="数据库名" value={dbDraft.database_name} onChange={(e) => setDbDraft({ ...dbDraft, database_name: e.target.value })} className="rounded border bg-background px-2 py-1" />
                    <input aria-label="数据库用户名" placeholder="用户名" value={dbDraft.username} onChange={(e) => setDbDraft({ ...dbDraft, username: e.target.value })} className="rounded border bg-background px-2 py-1" />
                    <input aria-label="数据库密码" type="password" placeholder="密码" value={dbDraft.password} onChange={(e) => setDbDraft({ ...dbDraft, password: e.target.value })} className="sm:col-span-2 rounded border bg-background px-2 py-1" />
                  </>
                )}
                <div className="sm:col-span-2 flex gap-2">
                  <button type="button" onClick={onTestDbDraft} disabled={disabled} className="rounded border px-3 py-1">{busy === "db-test" ? "测试中…" : "测试连接"}</button>
                  <button type="button" onClick={onSaveDbDraft} disabled={disabled || !dbDraft.name} className="rounded bg-primary px-3 py-1 text-primary-foreground">{busy === "db-save" ? "保存中…" : "保存连接"}</button>
                </div>
              </div>
            )}
            {dbSchema && (
              <>
                <label className="sm:col-span-2">
                  <span className="mb-1 block text-xs text-muted-foreground">表</span>
                  <select
                    aria-label="表"
                    value={selectedTable}
                    onChange={(e) => setSelectedTable(e.target.value)}
                    className="w-full rounded border bg-background px-3 py-2"
                  >
                    <option value="">-- 选表 --</option>
                    {dbSchema.tables.map((t: any) => (
                      <option key={t.name} value={t.name}>
                        {t.name}{" "}
                        {t.primary_key.length > 0
                          ? `(PK: ${t.primary_key.join(", ")})`
                          : "(无主键)"}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="mb-1 block text-xs text-muted-foreground">水位线字段（可选）</span>
                  <select
                    aria-label="水位线字段"
                    value={dbWatermarkField}
                    onChange={(e) => setDbWatermarkField(e.target.value)}
                    className="w-full rounded border bg-background px-3 py-2"
                  >
                    <option value="">-- 不设增量 --</option>
                    {selectedTable &&
                      dbSchema.tables
                        .find((t: any) => t.name === selectedTable)
                        ?.columns.map((c: any) => (
                          <option key={c.name} value={c.name}>
                            {c.name} ({c.type})
                          </option>
                        ))}
                  </select>
                </label>
                {selectedTable && (
                  <div className="sm:col-span-2">
                    <span className="mb-1 block text-xs text-muted-foreground">输出字段（不选表示全部）</span>
                    <div className="flex flex-wrap gap-3 rounded border p-2">
                      {dbSchema.tables.find((t: any) => t.name === selectedTable)?.columns.map((c: any) => (
                        <label key={c.name} className="flex items-center gap-1">
                          <input type="checkbox" checked={selectedFields.includes(c.name)} onChange={(e) => setSelectedFields(e.target.checked ? [...selectedFields, c.name] : selectedFields.filter((x) => x !== c.name))} />
                          {c.name}
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                <label>
                  <span className="mb-1 block text-xs text-muted-foreground">时间范围字段（可选）</span>
                  <select aria-label="时间范围字段" value={dbTimeField} onChange={(e) => setDbTimeField(e.target.value)} className="w-full rounded border bg-background px-3 py-2">
                    <option value="">-- 不限制 --</option>
                    {selectedTable && dbSchema.tables.find((t: any) => t.name === selectedTable)?.columns.map((c: any) => <option key={c.name} value={c.name}>{c.name}</option>)}
                  </select>
                </label>
                {dbTimeField && (
                  <div className="grid grid-cols-2 gap-2">
                    <input aria-label="开始时间" type="datetime-local" value={dbTimeStart} onChange={(e) => setDbTimeStart(e.target.value)} className="rounded border bg-background px-2 py-1" />
                    <input aria-label="结束时间" type="datetime-local" value={dbTimeEnd} onChange={(e) => setDbTimeEnd(e.target.value)} className="rounded border bg-background px-2 py-1" />
                  </div>
                )}
              </>
            )}
            <p className="col-span-2 text-xs text-muted-foreground">
              仅读取数据，不修改源库。首版不支持自定义 SQL 输入。
            </p>
          </div>
        )}
      </section>

      {/* 2. 预览 */}
      {currentSource() && (
        <section className="rounded border p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-semibold">2. 预览</h2>
            <button
              onClick={onPreview}
              disabled={disabled}
              className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
            >
              {busy === "preview" ? "预览中…" : "预览"}
            </button>
          </div>
          {preview && (
            <div className="space-y-3">
              <div className="text-sm">
                预计 {preview.estimated_records} 条记录，{preview.estimated_bytes} 字节
                {preview.parser_warnings.length > 0 && (
                  <span className="text-amber-600">
                    （{preview.parser_warnings.length} 条解析告警）
                  </span>
                )}
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">Schema</div>
                <div className="flex flex-wrap gap-2">
                  {preview.schema.fields.map((f) => (
                    <span key={f.name} className="rounded bg-muted px-2 py-0.5 text-xs">
                      {f.name}: {f.dtype}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  样本（前 {preview.sample.length} 条）
                </div>
                <div className="overflow-x-auto rounded border">
                  <table className="w-full text-xs">
                    <thead className="bg-muted">
                      <tr>
                        {preview.schema.fields.slice(0, 6).map((f) => (
                          <th key={f.name} className="p-1 text-left">{f.name}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.sample.map((row, i) => (
                        <tr key={i} className="border-t">
                          {preview.schema.fields.slice(0, 6).map((f) => (
                            <td key={f.name} className="max-w-xs truncate p-1">
                              {String(row[f.name] ?? "")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {/* 3. 执行 */}
      {(currentSource() || task) && (
        <section className="rounded border p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-semibold">3. 执行</h2>
            {currentSource() && (
              <button
                onClick={onCreate}
                disabled={disabled}
                className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
              >
                {busy === "create" ? "执行中…" : "执行任务"}
              </button>
            )}
          </div>
          {task && (
            <div className="space-y-2 text-sm">
              <div>状态：{STATUS_LABEL[task.status] || task.status}</div>
              {task.error && (
                <div className="text-red-600">
                  错误：{productText(task.error)}
                </div>
              )}
              {task.record_counts && Object.keys(task.record_counts).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(task.record_counts).map(([k, v]) => (
                    <span key={k} className="rounded bg-muted px-2 py-0.5 text-xs">
                      {k}: {v}
                    </span>
                  ))}
                </div>
              )}
              {task.quality && (
                <div className="rounded bg-muted p-2 text-xs">
                  质量：{String(task.quality.overall || "已生成")}
                </div>
              )}
              {(task.record_counts.rejects_parse || 0) > 0 && (
                <div className="text-amber-700">
                  解析隔离：{task.record_counts.rejects_parse} 条
                  <button
                    onClick={() => downloadFile(`/api/downloads/${task.task_id}/rejects/parse_rejects.jsonl`, "parse_rejects.jsonl")}
                    className="ml-2 text-primary hover:underline"
                  >
                    下载 rejects
                  </button>
                </div>
              )}
              {(task.status === "SUCCEEDED" || task.status === "SUCCEEDED_WITH_WARNINGS") && (
                <button
                  onClick={onRerun}
                  disabled={disabled}
                  className="rounded border px-2 py-0.5 text-xs disabled:opacity-50"
                >
                  {busy === "rerun" ? "复跑中…" : "复跑"}
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {/* 4. 产物 */}
      {manifest && (
        <section className="rounded border p-4">
          <h2 className="mb-2 font-semibold">4. 产物下载</h2>
          <div className="mb-2 text-sm">
            <button
              onClick={() => downloadFile(`/api/downloads/${manifest.task_id}/manifest.json`, "manifest.json")}
              className="text-primary hover:underline"
            >
              下载 Manifest
            </button>
          </div>
          <div className="space-y-1 text-sm">
            {manifest.outputs.map((o) => (
              <div key={o.path} className="flex items-center justify-between">
                <span>
                  {o.format}（{o.records} 条）
                </span>
                <button
                  onClick={() =>
                    downloadFile(
                      `/api/downloads/${o.path}`,
                      o.path.split("/").pop() || o.path,
                    )
                  }
                  className="text-primary hover:underline"
                >
                  下载
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
