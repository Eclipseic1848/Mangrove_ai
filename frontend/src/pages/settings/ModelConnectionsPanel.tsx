import { useEffect, useMemo, useRef, useState } from "react";
import { EVENTS, Joyride, type Step } from "react-joyride";
import {
  SiAlibabacloud,
  SiAnthropic,
  SiGooglegemini,
  SiOpenai,
} from "@icons-pack/react-simple-icons";
import {
  EyeOff,
  KeyRound,
  LifeBuoy,
  Loader2,
  Plus,
  Server,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, ApiError } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

interface ProviderPreset {
  preset_id: string;
  version: string;
  display_name: string;
  description: string;
  recommended_model: string;
  models: string[];
  model_catalog?: Array<{
    model_id: string;
    display_name: string;
    role: string;
    verified_on?: string;
    source_url?: string;
    release_status?: string;
  }>;
  help_url: string;
  key_url?: string;
  region_note?: string;
  regions?: Array<{ id: string; label: string; workspace_required: boolean }>;
  catalog_stale?: boolean;
}

interface ConnectionModel {
  model_id: string;
  display_name: string;
  catalog_role: string;
  catalog_version: string;
  status: string;
  enabled: boolean;
  is_default: boolean;
  verified_at?: string | null;
  error_code?: string | null;
  usage_status: string;
}

interface ModelConnection {
  connection_id: string;
  owner_scope: "user_personal" | "platform_shared";
  preset_id?: string | null;
  display_name: string;
  model: string;
  api_format: string;
  locality: string;
  status: string;
  key_hint?: string;
  verified_at?: string | null;
  default_model?: string | null;
  available_model_count?: number;
  models?: ConnectionModel[];
}

const TOUR_STEPS: Step[] = [
  {
    target: '[data-model-tour="default-connection"]',
    title: "先确认新任务默认模型",
    content: "这里持续显示默认连接、模型和范围；失效时会明确要求重新选择。",
    placement: "bottom",
  },
  {
    target: '[data-model-tour="provider"]',
    title: "选择模型服务商",
    content: "平台已经准备好地址和协议，默认不需要理解技术字段。",
    placement: "bottom",
  },
  {
    target: '[data-model-tour="key"]',
    title: "填写自己的 API Key",
    content: "Key 在线加密保存，页面只显示是否配置和尾部遮罩。",
    placement: "bottom",
  },
  {
    target: '[data-model-tour="verify"]',
    title: "验证推荐模型",
    content: "平台发送一条合成测试内容，可能产生少量 Provider 原生用量。",
    placement: "left",
  },
  {
    target: '[data-model-tour="complete"]',
    title: "保存后即可选择",
    content: "任务执行前仍会展示连接和外发数据类别，不会静默切换其他连接。",
    placement: "top",
  },
  {
    target: "#model-connection-list",
    title: "连接可以有多套",
    content: "同一 Provider 的日常、备用或不同账户会按名称、范围和状态分别展示。",
    placement: "top",
  },
];

const API_FORMATS = [
  ["openai_chat_completions", "OpenAI Chat Completions"],
  ["openai_responses", "OpenAI Responses API"],
  ["anthropic_messages", "Anthropic Messages"],
  ["gemini_generate_content", "Gemini generateContent"],
] as const;

const MODEL_STATUS_LABELS: Record<string, string> = {
  pending_validation: "待验证",
  validating: "验证中",
  available: "可用",
  model_access_denied: "无模型权限",
  credentials_invalid: "凭证无效",
  protocol_incompatible: "协议不兼容",
  rate_limited: "限流",
  network_unreachable: "网络不可达",
  balance_insufficient: "API 额度不足",
  result_unknown: "验证结果未知",
  disabled: "已停用",
};

const MODEL_ROLE_HINTS: Record<string, string> = {
  balanced: "通用问答、整理资料与日常任务，兼顾响应速度和效果。",
  quality: "适合复杂分析和较难任务；费用与等待时间请以官方说明为准。",
  efficiency: "适合快速问答和高频轻量任务。",
  coding: "适合代码理解与编程任务。",
  vision: "适合图文相关需求；当前连接测试只验证文本，图像能力需另行验证。",
};

function ProviderMark({ id }: { id: string }) {
  const icons: Record<string, typeof SiOpenai> = {
    qwen: SiAlibabacloud,
    openai: SiOpenai,
    anthropic: SiAnthropic,
    gemini: SiGooglegemini,
  };
  const Icon = icons[id];
  const initials: Record<string, string> = {
    deepseek: "DS",
    qwen: "QW",
    openai: "OA",
    anthropic: "AN",
    gemini: "GM",
    kimi: "KM",
    zhipu: "GL",
  };
  return (
    <span
      className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-foreground text-[11px] font-semibold text-background"
      aria-hidden="true"
    >
      {Icon
        ? <Icon size={18} title="" />
        : initials[id] || id.slice(0, 2).toUpperCase()}
    </span>
  );
}

function suggestedPersonalName(
  preset: ProviderPreset,
  connections: ModelConnection[],
) {
  const sequence = connections.filter(
    (item) =>
      item.owner_scope === "user_personal"
      && item.preset_id === preset.preset_id,
  ).length + 1;
  return sequence === 1
    ? `${preset.display_name} 连接`
    : `${preset.display_name} 连接 ${sequence}`;
}

export function ModelConnectionsPanel({ isManager }: { isManager: boolean }) {
  const { theme } = useTheme();
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [connections, setConnections] = useState<ModelConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [scope, setScope] = useState<"personal" | "platform">("personal");
  const [showSetup, setShowSetup] = useState(false);
  const [personalName, setPersonalName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [setupError, setSetupError] = useState("");
  const [setupUnknown, setSetupUnknown] = useState(false);
  const [localInfo, setLocalInfo] = useState(false);
  const [savedConnection, setSavedConnection] = useState<ModelConnection | null>(null);
  const submission = useRef(false);
  const [saving, setSaving] = useState(false);
  const [modelAction, setModelAction] = useState("");
  const [preference, setPreference] = useState<{
    connection_id: string;
    model_id: string;
    available: boolean;
  } | null>(null);
  const [tourRun, setTourRun] = useState(false);
  const [managedOpen, setManagedOpen] = useState(false);
  const [managedMode, setManagedMode] = useState<"preset" | "custom">("preset");
  const [localService, setLocalService] = useState("custom");
  const [platformRegion, setPlatformRegion] = useState("");
  const [platformWorkspace, setPlatformWorkspace] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ModelConnection | null>(null);
  const [platformPreset, setPlatformPreset] = useState({
    display_name: "",
    preset_id: "",
    model: "",
    api_key: "",
  });
  const [managed, setManaged] = useState({
    display_name: "",
    base_url: "",
    api_format: "openai_chat_completions",
    model: "",
    model_ids_text: "",
    api_key: "",
  });
  const [discovery, setDiscovery] = useState<{
    models: string[];
    detected_api_formats: string[];
    recommended_api_format?: string | null;
    manual_models_required: boolean;
  } | null>(null);

  const selectedPreset = useMemo(
    () => presets.find((item) => item.preset_id === selectedPresetId) ?? presets[0],
    [presets, selectedPresetId],
  );
  const selectedPlatformPreset = useMemo(
    () =>
      presets.find((item) => item.preset_id === platformPreset.preset_id)
      ?? presets[0],
    [platformPreset.preset_id, presets],
  );
  const personalConnections = connections.filter((item) => item.owner_scope === "user_personal");
  const platformConnections = connections.filter((item) => item.owner_scope === "platform_shared");

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [presetResult, connectionResult, onboardingResult, preferenceResult] =
        await Promise.allSettled([
        api.get("/api/model-connections/presets"),
        api.get("/api/model-connections"),
        api.get("/api/settings/onboarding/model-connections"),
        api.get("/api/model-connections/preferences/default"),
      ]);
      const errors: string[] = [];
      if (presetResult.status === "rejected") {
        errors.push(
          presetResult.reason?.message || "Provider 预设加载失败",
        );
      }
      if (connectionResult.status === "rejected") {
        errors.push(
          connectionResult.reason?.message || "连接列表加载失败",
        );
      }
      const presetData = presetResult.status === "fulfilled"
        ? presetResult.value
        : { items: presets };
      const connectionData = connectionResult.status === "fulfilled"
        ? connectionResult.value
        : { items: connections };
      if (errors.length) {
        const message = `${errors.join("；")}，已保留其他区域和当前数据`;
        setLoadError(message);
        toast.error(message);
      }
      const onboarding = onboardingResult.status === "fulfilled"
        ? onboardingResult.value
        : { state: "completed" };
      if (preferenceResult.status === "fulfilled") {
        setPreference(preferenceResult.value.preference ?? null);
      }
      const nextPresets = (presetData.items || []) as ProviderPreset[];
      const nextConnections = (connectionData.items || []) as ModelConnection[];
      setPresets(nextPresets);
      setConnections(nextConnections);
      if (!selectedPresetId && nextPresets[0]) {
        setSelectedPresetId(nextPresets[0].preset_id);
        setSelectedModel(nextPresets[0].recommended_model);
      }
      if (!personalName && nextPresets[0]) {
        setPersonalName(suggestedPersonalName(nextPresets[0], nextConnections));
      }
      if (!platformPreset.preset_id && nextPresets[0]) {
        setPlatformPreset((current) => ({
          ...current,
          preset_id: nextPresets[0].preset_id,
          model: nextPresets[0].recommended_model,
        }));
      }
      if (nextConnections.length === 0) {
        setShowSetup(true);
        if (onboarding.state === "not_started") {
          window.setTimeout(() => setTourRun(true), 200);
        }
      }
    } catch (error: any) {
      const message = error.message || "模型连接加载失败";
      setLoadError(message);
      setShowSetup(true);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (selectedPreset) {
      setSelectedModel(selectedPreset.recommended_model);
      setRegion(selectedPreset.regions?.[0]?.id || "");
      setWorkspaceId("");
      setApiKey("");
      setSetupError("");
    }
  }, [selectedPreset?.preset_id]);

  const rememberTour = async (state: "completed" | "skipped") => {
    try {
      await api.put("/api/settings/onboarding/model-connections", { state });
    } catch {
      // 引导状态失败不阻断连接配置，用户仍可从页面重新播放。
    }
  };

  const savePersonal = async () => {
    if (!selectedPreset || !personalName.trim() || !apiKey.trim() || submission.current || setupUnknown) return;
    submission.current = true;
    setSaving(true);
    setSetupError("");
    try {
      const saved = await api.post(`/api/model-connections/presets/${selectedPreset.preset_id}`, {
        display_name: personalName.trim(),
        api_key: apiKey.trim(),
        model: selectedModel || selectedPreset.recommended_model,
        region: region || null,
        workspace_id: workspaceId.trim(),
      });
      setApiKey("");
      setPersonalName("");
      setShowSetup(false);
      setSavedConnection(saved);
      const total = Array.isArray(saved.models) ? saved.models.length : 0;
      toast.success(
        total
          ? `连接已保存，${saved.available_model_count} / ${total} 个模型可用`
          : "连接已验证并保存",
      );
      await rememberTour("completed");
      await load();
    } catch (error: any) {
      const unknown = !(error instanceof ApiError) || error.status >= 500 || error.message.includes("未知");
      setSetupUnknown(unknown);
      setSetupError(unknown ? "结果尚不确定，可能已保存或产生用量。请先检查连接列表和服务商记录，确认后再重试。" : error.message || "连接验证失败，请检查密钥和模型后重试");
    } finally {
      submission.current = false;
      setSaving(false);
    }
  };

  const saveManaged = async () => {
    if (!managed.display_name.trim() || !managed.base_url.trim() || !managed.model.trim() || submission.current || setupUnknown) return;
    submission.current = true;
    setSetupError("");
    setSaving(true);
    try {
      const models = managed.model_ids_text
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 8);
      const saved = await api.post("/api/model-connections/managed", {
        display_name: managed.display_name,
        base_url: managed.base_url,
        api_format: managed.api_format,
        model: managed.model,
        models: localService === "custom" && models.length ? models : [managed.model],
        api_key: managed.api_key,
      });
      toast.success("平台连接已验证并发布");
      setManagedOpen(false);
      setSavedConnection(saved);
      setShowSetup(false);
      setScope("platform");
      setManaged({
        display_name: "",
        base_url: "",
        api_format: "openai_chat_completions",
        model: "",
        model_ids_text: "",
        api_key: "",
      });
      await load();
    } catch (error: any) {
      const unknown = !(error instanceof ApiError) || error.status >= 500 || error.message.includes("未知");
      setSetupUnknown(unknown);
      setSetupError(unknown ? "结果尚不确定，可能已保存或产生用量。请先检查连接列表和服务记录，再决定是否重试。" : error.message || "连接失败，请检查服务是否已启动、地址和模型 ID");
    } finally {
      submission.current = false;
      setSaving(false);
    }
  };

  const discoverManaged = async () => {
    if (submission.current) return;
    submission.current = true;
    setSaving(true);
    setSetupError("");
    try {
      const manual = managed.model_ids_text
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter(Boolean)
        .slice(0, 8);
      const result = await api.post("/api/model-connections/managed/discover", {
        base_url: managed.base_url,
        api_key: managed.api_key,
        model_ids: manual,
        probe_protocols: localService === "custom",
      });
      setDiscovery(result);
      setManaged((current) => ({
        ...current,
        model_ids_text: result.models.join("\n"),
        model: current.model || result.models[0] || "",
        api_format: result.recommended_api_format || current.api_format,
      }));
      toast.success(
        localService !== "custom" ? (result.models.length ? "已读取模型列表，尚未测试推理" : "服务未提供模型列表，请手动填写模型 ID") : result.detected_api_formats.length
          ? `检测到 ${result.detected_api_formats.length} 种可用协议`
          : "未自动识别协议，请确认模型 ID 和 API 格式",
      );
    } catch (error: any) {
      setSetupError(error.message || "无法读取模型列表，请检查服务地址和鉴权；也可以手动填写模型 ID");
    } finally {
      submission.current = false;
      setSaving(false);
    }
  };

  const savePlatformPreset = async () => {
    if (
      submission.current || setupUnknown || !selectedPlatformPreset
      || !platformPreset.display_name.trim()
      || !platformPreset.api_key.trim()
      || !platformPreset.model
    ) return;
    submission.current = true;
    setSetupError("");
    setSaving(true);
    try {
      const saved = await api.post(
        `/api/model-connections/managed/presets/${selectedPlatformPreset.preset_id}`,
        {
          display_name: platformPreset.display_name.trim(),
          model: platformPreset.model,
          api_key: platformPreset.api_key.trim(),
          region: platformRegion || null,
          workspace_id: platformWorkspace.trim(),
        },
      );
      const total = Array.isArray(saved.models) ? saved.models.length : 0;
      toast.success(
        total
          ? `平台连接已发布，${saved.available_model_count} / ${total} 个模型可用`
          : "平台 Provider 连接已验证并发布",
      );
      setManagedOpen(false);
      setPlatformPreset({
        display_name: "",
        preset_id: selectedPlatformPreset.preset_id,
        model: selectedPlatformPreset.recommended_model,
        api_key: "",
      });
      await load();
    } catch (error: any) {
      const unknown = !(error instanceof ApiError) || error.status >= 500 || /未知/.test(error.message);
      setSetupUnknown(unknown);
      setSetupError(unknown ? "结果未知：请先检查连接列表和服务商用量记录，避免立即重复计费。" : error.message || "平台连接验证失败，请检查密钥、地域和模型权限");
    } finally {
      submission.current = false;
      setSaving(false);
    }
  };

  const openPlatformConnection = () => {
    const preset = selectedPlatformPreset ?? presets[0];
    setManagedMode("preset");
    setLocalService("custom");
    setSetupError("");
    setSetupUnknown(false);
    if (preset) {
      setPlatformRegion(preset.regions?.[0]?.id || "");
      setPlatformWorkspace("");
      setPlatformPreset((current) => ({
        ...current,
        preset_id: preset.preset_id,
        model: current.model || preset.recommended_model,
        api_key: "",
      }));
    }
    setManagedOpen(true);
  };

  const deleteConnection = async () => {
    if (!deleteTarget) return;
    try {
      await api.del(`/api/model-connections/${deleteTarget.connection_id}`);
      toast.success("连接已删除");
      setDeleteTarget(null);
      await load();
    } catch (error: any) {
      toast.error(error.message || "删除失败");
    }
  };

  const retryModel = async (
    connection: ModelConnection,
    model: ConnectionModel,
  ) => {
    const action = `retry:${connection.connection_id}:${model.model_id}`;
    setModelAction(action);
    try {
      await api.post(
        `/api/model-connections/${connection.connection_id}/models/retry`,
        { model_ids: [model.model_id] },
      );
      toast.success(`${model.display_name} 已重新验证`);
      await load();
    } catch (error: any) {
      toast.error(error.message || "模型重试失败");
    } finally {
      setModelAction("");
    }
  };

  const verifyImported = async (connection: ModelConnection) => {
    const retryable = (connection.models || [])
      .filter((item) => !["available", "disabled", "validating"].includes(item.status))
      .map((item) => item.model_id);
    if (!retryable.length) {
      toast.info("当前没有需要重新验证的模型");
      return;
    }
    setModelAction(`import:${connection.connection_id}`);
    try {
      await api.post(
        `/api/model-connections/${connection.connection_id}/models/retry`,
        { model_ids: retryable.slice(0, 8) },
      );
      toast.success("导入连接已验证，无需重新填写 Key");
      await load();
    } catch (error: any) {
      toast.error(error.message || "导入连接验证失败");
    } finally {
      setModelAction("");
    }
  };

  const changeDefaultModel = async (
    connection: ModelConnection,
    model: ConnectionModel,
  ) => {
    const action = `default:${connection.connection_id}:${model.model_id}`;
    setModelAction(action);
    try {
      await api.put(
        `/api/model-connections/${connection.connection_id}/default-model`,
        { model: model.model_id },
      );
      toast.success(`默认模型已改为 ${model.display_name}`);
      await load();
    } catch (error: any) {
      toast.error(error.message || "默认模型修改失败");
    } finally {
      setModelAction("");
    }
  };

  const toggleModel = async (
    connection: ModelConnection,
    model: ConnectionModel,
    enabled: boolean,
  ) => {
    const action = `toggle:${connection.connection_id}:${model.model_id}`;
    setModelAction(action);
    try {
      await api.patch(
        `/api/model-connections/${connection.connection_id}/models/${
          encodeURIComponent(model.model_id)
        }`,
        { enabled },
      );
      toast.success(`${model.display_name} 已${enabled ? "启用" : "停用"}`);
      await load();
    } catch (error: any) {
      toast.error(error.message || "模型状态修改失败");
    } finally {
      setModelAction("");
    }
  };

  const togglePlatformConnection = async (
    connection: ModelConnection,
    enabled: boolean,
  ) => {
    const action = `connection:${connection.connection_id}`;
    setModelAction(action);
    try {
      await api.patch(`/api/model-connections/${connection.connection_id}`, { enabled });
      toast.success(`平台连接已${enabled ? "启用" : "停用"}`);
      await load();
    } catch (error: any) {
      toast.error(error.message || "平台连接状态修改失败");
    } finally {
      setModelAction("");
    }
  };

  const setUserDefault = async (
    connection: ModelConnection,
    model: ConnectionModel,
  ) => {
    const action = `preference:${connection.connection_id}:${model.model_id}`;
    setModelAction(action);
    try {
      await api.put("/api/model-connections/preferences/default", {
        connection_id: connection.connection_id,
        model_id: model.model_id,
      });
      toast.success(`默认连接已设为 ${connection.display_name} · ${model.display_name}`);
      await load();
    } catch (error: any) {
      toast.error(error.message || "默认连接设置失败");
    } finally {
      setModelAction("");
    }
  };

  const startTour = () => {
    setScope("personal");
    const preset = selectedPreset ?? presets[0];
    if (preset && !personalName) {
      setPersonalName(suggestedPersonalName(preset, connections));
    }
    setShowSetup(true);
    window.setTimeout(() => setTourRun(true), 100);
  };

  const importLegacy = async () => {
    setModelAction("import-legacy");
    try {
      const result = await api.post("/api/model-connections/imports/legacy", {});
      toast.success(
        result.items?.length
          ? `已发现 ${result.items.length} 套旧配置，Key 无需重填`
          : "没有发现可导入的旧模型配置",
      );
      setShowSetup(false);
      await load();
    } catch (error: any) {
      toast.error(error.message || "旧配置导入失败");
    } finally {
      setModelAction("");
    }
  };

  const openPersonalConnection = (presetId?: string | null) => {
    const preset = presets.find((item) => item.preset_id === presetId)
      ?? selectedPreset
      ?? presets[0];
    if (preset) {
      setSelectedPresetId(preset.preset_id);
      setSelectedModel(preset.recommended_model);
      setPersonalName(suggestedPersonalName(preset, connections));
    }
    setApiKey("");
    setSetupError("");
    setSetupUnknown(false);
    setShowSetup(true);
  };

  const visibleConnections = scope === "platform"
    ? platformConnections
    : personalConnections;

  return (
    <div className="space-y-5">
      <Joyride
        run={tourRun}
        continuous
        scrollToFirstStep
        steps={TOUR_STEPS}
        locale={{
          back: "上一步",
          close: "关闭",
          last: "完成",
          next: "下一步",
          nextWithProgress: "下一步（{current}/{total}）",
          open: "打开引导",
          skip: "跳过",
        }}
        options={{
          buttons: ["back", "skip", "primary"],
          showProgress: true,
          skipBeacon: true,
          overlayClickAction: false,
          closeButtonAction: "skip",
          primaryColor: theme === "dark" ? "#a78bfa" : "#6d28d9",
          backgroundColor: theme === "dark" ? "#18181b" : "#ffffff",
          arrowColor: theme === "dark" ? "#18181b" : "#ffffff",
          textColor: theme === "dark" ? "#fafafa" : "#18181b",
          overlayColor: "rgba(9, 9, 11, 0.68)",
          zIndex: 140,
        }}
        onEvent={(event) => {
          if (event.type === EVENTS.TOUR_END) {
            setTourRun(false);
            rememberTour(event.action === "skip" ? "skipped" : "completed");
          }
        }}
      />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">模型与连接</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            个人 Key 只供自己的任务使用；平台共享连接不会向你公开内部端点或凭证。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => void importLegacy()}>
            导入现有配置
          </Button>
          <Button variant="outline" size="sm" onClick={startTour} className="gap-1.5">
            <LifeBuoy className="h-4 w-4" /> 播放新手引导
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2" aria-label="模型接入方式">
        <Button variant="outline" disabled={saving} onClick={() => { setLocalInfo(false); openPersonalConnection(); }}>云端服务商</Button>
        <Button variant="outline" disabled={saving} onClick={() => setLocalInfo(true)}>本地或局域网模型</Button>
      </div>
      {localInfo && <div className="space-y-3 rounded-lg border p-4 text-sm">
        <h3 className="font-semibold">连接已经运行的本地模型</h3>
        <p>支持 Ollama、LM Studio 及兼容 OpenAI 接口的服务（如 vLLM）。先启动模型服务，再填写地址并选择模型。</p>
        <p>localhost 指运行 Mangrove 后端的电脑。若平台在服务器或容器中，请填写后端能访问的模型服务地址；不会自动扫描设备或修改防火墙。</p>
        <p>{isManager ? "本地服务登记为平台连接，获准用户可使用。只测试明确选定的模型；无鉴权服务无需填写密钥。" : "本地地址由管理员登记，以免普通账户任意访问服务器内网。请把服务类型、地址和模型名称交给管理员；密钥请由管理员在配置页面填写。登记后可在“平台可用连接”中选择。"}</p>
        {isManager && <Button onClick={() => { setLocalService("ollama"); setManagedMode("custom"); setManaged({ display_name: "Ollama 本地模型", base_url: "http://localhost:11434/v1", api_format: "openai_chat_completions", model: "", model_ids_text: "", api_key: "" }); setDiscovery(null); setSetupError(""); setSetupUnknown(false); setManagedOpen(true); }}>登记本地模型</Button>}
        <Button variant="ghost" onClick={() => { setScope("platform"); setShowSetup(false); setLocalInfo(false); }}>查看平台可用连接</Button>
      </div>}
      {savedConnection && <div role="status" className="space-y-2 rounded-lg border border-primary/30 p-4 text-sm">
        <p>已保存：{savedConnection.display_name} · {savedConnection.default_model || savedConnection.model}</p>
        <p>所选模型已通过文本连接测试；工具任务能力需要另外验证。其他目录模型未自动启用。</p>
        <Button disabled={!!modelAction} onClick={() => { const model = savedConnection.models?.find((item) => item.is_default); if (model) void setUserDefault(savedConnection, model); }}>用于新任务</Button>
      </div>}

      <Card data-model-tour="default-connection" className="border-primary/25">
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div>
            <p className="text-xs font-medium text-muted-foreground">新任务默认模型</p>
            {preference ? (
              <p className="mt-1 font-medium">
                {connections.find((item) => item.connection_id === preference.connection_id)
                  ?.display_name || "原默认连接"}
                {" · "}
                {connections
                  .find((item) => item.connection_id === preference.connection_id)
                  ?.models?.find((item) => item.model_id === preference.model_id)
                  ?.display_name || preference.model_id}
                {" · "}
                {preference.available ? "可用" : "需要重新选择"}
              </p>
            ) : (
              <p className="mt-1 font-medium">尚未设置，请从下方可用模型中选择</p>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => document.getElementById("model-connection-list")?.focus()}
          >
            更换默认连接
          </Button>
        </CardContent>
      </Card>

      {
        <div role="tablist" aria-label="模型连接范围" className="inline-flex rounded-lg border bg-muted/30 p-1">
          <button
            type="button"
            role="tab"
            data-model-tour="platform-connections"
            aria-selected={scope === "personal"}
            onClick={() => setScope("personal")}
            className={cn(
              "rounded-md px-4 py-1.5 text-sm",
              scope === "personal" ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
            )}
          >
            {isManager ? "个人连接" : "我的连接"}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "platform"}
            onClick={() => {
              setScope("platform");
              setShowSetup(false);
            }}
            className={cn(
              "rounded-md px-4 py-1.5 text-sm",
              scope === "platform" ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
            )}
          >
            {isManager ? "平台连接" : "平台可用连接"}
          </button>
        </div>
      }

      {scope === "personal" && showSetup ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold">连接一个模型服务</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    选择服务商，按官方指引获取密钥，再选择并测试一个模型。
                  </p>
                </div>
                {connections.length > 0 && (
                  <Button variant="ghost" size="sm" onClick={() => setShowSetup(false)}>返回列表</Button>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset disabled={saving} className="space-y-5">
              {loadError && (
                <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-4">
                  <p className="font-medium text-destructive">模型连接加载失败</p>
                  <p className="mt-1 text-sm text-muted-foreground">{loadError}</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void load()}
                    className="mt-3"
                  >
                    重新加载
                  </Button>
                </div>
              )}
              {!loadError && (
                <div>
                  <label
                    htmlFor="personal-connection-name"
                    className="mb-1.5 block text-sm font-medium"
                  >
                    连接名称 <span className="text-destructive">*</span>
                  </label>
                  <Input
                    id="personal-connection-name"
                    value={personalName}
                    onChange={(event) => setPersonalName(event.target.value)}
                    placeholder="例如：DeepSeek 日常"
                    maxLength={80}
                  />
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    名称已自动填写，可按用途修改。
                  </p>
                </div>
              )}
              {!loadError && (
                <div data-model-tour="provider">
                  <label
                    htmlFor="personal-provider"
                    className="mb-1.5 block text-sm font-medium"
                  >
                    模型服务商 <span className="text-destructive">*</span>
                  </label>
                  <select
                    id="personal-provider"
                    value={selectedPreset?.preset_id || ""}
                    onChange={(event) => {
                      const preset = presets.find(
                        (item) => item.preset_id === event.target.value,
                      );
                      setSelectedPresetId(event.target.value);
                      if (preset) {
                        setSelectedModel(preset.recommended_model);
                        setPersonalName(suggestedPersonalName(preset, connections));
                      }
                    }}
                    className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                  >
                    {presets.map((item) => (
                      <option key={item.preset_id} value={item.preset_id}>
                        {item.display_name}
                      </option>
                    ))}
                  </select>
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    服务地址与接口已自动填写。
                  </p>
                </div>
              )}

              {selectedPreset?.regions && selectedPreset.regions.length > 0 && <div className="space-y-3">
                <label htmlFor="personal-region" className="block text-sm font-medium">密钥所属地域</label>
                <select id="personal-region" value={region} onChange={(event) => { setRegion(event.target.value); setApiKey(""); setWorkspaceId(""); }} className="h-10 w-full rounded-md border bg-background px-3 text-sm">
                  {selectedPreset.regions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                </select>
                {selectedPreset.regions.find((item) => item.id === region)?.workspace_required && <>
                  <label htmlFor="personal-workspace" className="block text-sm font-medium">业务空间 ID</label>
                  <Input id="personal-workspace" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} placeholder="从百炼控制台复制，无需填写网址" maxLength={63} />
                </>}
              </div>}
              {selectedPreset?.region_note && <p className="text-xs leading-5 text-muted-foreground">{selectedPreset.region_note}</p>}
              {selectedPreset?.catalog_stale && <p role="status" className="text-sm text-destructive">目录超过 30 天未核验，请先查看官方目录确认模型仍可用。</p>}
              {setupError && !managedOpen && <div role="alert" className="space-y-2 text-sm text-destructive">
                <p>{setupError}</p>
                {setupUnknown && <Button variant="outline" onClick={() => { setShowSetup(false); void load(); }}>先检查连接列表</Button>}
              </div>}
              {!loadError && selectedPreset && (
                <>
                  <div>
                    <label htmlFor="personal-model" className="mb-1.5 block text-sm font-medium">
                      选择模型 <span className="text-destructive">*</span>
                    </label>
                    <select
                      id="personal-model"
                      value={selectedModel}
                      onChange={(event) => setSelectedModel(event.target.value)}
                      className="h-10 w-full rounded-md border bg-background px-3 text-sm"
                    >
                      {selectedPreset.models.map((model) => (
                        <option key={model} value={model}>
                          {selectedPreset.model_catalog?.find(
                            (item) => item.model_id === model,
                          )?.display_name || model}
                          {model === selectedPreset.recommended_model ? "（平台推荐）" : ""}
                        </option>
                      ))}
                    </select>
                  </div>
                  <p className="break-words text-xs text-muted-foreground">
                    {MODEL_ROLE_HINTS[selectedPreset.model_catalog?.find((item) => item.model_id === selectedModel)?.role || ""]}
                    {selectedModel === selectedPreset.recommended_model && " 推荐是平台按上述用途给出的起点，不代表你的任务评测或账户资格已通过。"}
                    {selectedPreset.model_catalog?.find((item) => item.model_id === selectedModel)?.verified_on && <>目录核验：{selectedPreset.model_catalog.find((item) => item.model_id === selectedModel)?.verified_on} · </>}
                    <a href={selectedPreset.model_catalog?.find((item) => item.model_id === selectedModel)?.source_url || selectedPreset.help_url} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">查看官方模型说明</a>。列入目录不代表你的账户已获权限。
                  </p>
                  <div data-model-tour="key">
                    <label htmlFor="personal-api-key" className="mb-1.5 block text-sm font-medium">
                      API Key <span className="text-destructive">*</span>
                    </label>
                    <div className="relative">
                      <KeyRound className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="personal-api-key"
                        type="password"
                        autoComplete="new-password"
                        value={apiKey}
                        onChange={(event) => setApiKey(event.target.value)}
                        placeholder="输入 API Key"
                        className="pl-9 pr-9"
                      />
                      <EyeOff className="pointer-events-none absolute right-3 top-3 h-4 w-4 text-muted-foreground" />
                    </div>
                    <div className="mt-1.5 flex justify-between gap-3 text-xs text-muted-foreground">
                      <span>保存后只显示尾部遮罩，不提供复制或导出。</span>
                      <a href={selectedPreset.key_url || selectedPreset.help_url} target="_blank" rel="noreferrer" className="shrink-0 text-primary hover:underline">
                        获取 Key
                      </a>
                    </div>
                  </div>
                  <Button
                    data-model-tour="complete"
                    disabled={!personalName.trim() || !apiKey.trim() || saving || setupUnknown || (!!selectedPreset.regions?.find((item) => item.id === region)?.workspace_required && !workspaceId.trim())}
                    onClick={savePersonal}
                    className="w-full"
                  >
                    {saving
                      ? <Loader2 className="h-4 w-4 animate-spin" />
                      : "测试并保存所选模型"}
                  </Button>
                </>
              )}
              </fieldset>
            </CardContent>
          </Card>

          <Card data-model-tour="verify" className="h-fit border-emerald-500/30 bg-emerald-500/[0.04]">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-emerald-500" /> 验证说明
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="rounded-lg bg-background/80 p-3">
                <div className="text-xs text-muted-foreground">服务商</div>
                <div className="mt-1 font-medium">{selectedPreset?.display_name || "—"}</div>
              </div>
              <div className="rounded-lg bg-background/80 p-3">
                <div className="text-xs text-muted-foreground">检查模型</div>
                <div className="mt-1 break-all font-medium">{selectedModel || "—"}</div>
              </div>
              <p className="text-xs leading-5 text-muted-foreground">
                仅向所选模型发送一次简短测试，可能按服务商标准计费；不发送任务正文或附件。连接测试不代表工具任务资格。
              </p>
            </CardContent>
          </Card>
        </div>
      ) : (
        <Card id="model-connection-list" tabIndex={-1}>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle className="text-base">
                  {scope === "platform" ? "平台共享连接" : "可用连接"}
                </CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">{visibleConnections.length} 个连接</p>
              </div>
              {scope === "platform" && isManager ? (
                <Button size="sm" onClick={openPlatformConnection} className="gap-1.5">
                  <Plus className="h-4 w-4" /> 添加平台连接
                </Button>
              ) : scope === "personal" ? (
                <Button size="sm" onClick={() => openPersonalConnection()} className="gap-1.5">
                  <Plus className="h-4 w-4" /> 添加个人连接
                </Button>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {loading ? (
              <p className="py-8 text-center text-sm text-muted-foreground">加载中…</p>
            ) : visibleConnections.length === 0 ? (
              <div className="rounded-xl border border-dashed p-8 text-center">
                <Server className="mx-auto h-8 w-8 text-muted-foreground" />
                <p className="mt-3 text-sm font-medium">
                  {scope === "platform" ? "还没有平台连接" : "还没有可用连接"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {scope === "platform"
                    ? "添加后，获准用户可以在任务中选择使用。"
                    : "添加一套个人连接后，就可以在自己的任务中选择使用。"}
                </p>
              </div>
            ) : (
              visibleConnections.map((connection) => (
                <div key={connection.connection_id} className="flex flex-wrap items-center gap-3 rounded-xl border p-3">
                  <ProviderMark id={connection.preset_id || "managed"} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{connection.display_name}</span>
                      <Badge variant={connection.status === "verified" ? "success" : "outline"}>
                        {connection.status === "verified"
                          ? "连接已验证"
                          : connection.status === "needs_default_model"
                            ? "请选择默认模型"
                            : connection.status === "disabled"
                              ? "连接已停用"
                              : connection.status}
                      </Badge>
                      <Badge variant="outline">
                        {connection.owner_scope === "user_personal" ? "仅自己可用" : "平台共享"}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {(connection.models?.length ?? 0) > 0
                        ? `${connection.available_model_count ?? 0} / ${connection.models?.length} 个模型可用`
                        : connection.model}
                      {connection.default_model
                        ? ` · 默认 ${
                            connection.models?.find((item) => item.is_default)
                              ?.display_name || connection.default_model
                          }`
                        : connection.status === "needs_default_model"
                          ? " · 需要选择默认模型"
                          : ""}
                      {connection.key_hint ? ` · Key •••• ${connection.key_hint}` : ""}
                    </div>
                    {(connection.models?.length ?? 0) > 0 && (
                      <div className="mt-3 space-y-2">
                        {connection.models?.map((model) => {
                          const actionSuffix = `${connection.connection_id}:${model.model_id}`;
                          const isBusy = modelAction.endsWith(actionSuffix);
                          const failed = ![
                            "available",
                            "disabled",
                            "validating",
                          ].includes(model.status);
                          return (
                            <div
                              key={model.model_id}
                              className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/35 px-3 py-2"
                            >
                              <span className="min-w-0 flex-1 text-sm font-medium">
                                {model.display_name}
                              </span>
                              <Badge
                                variant={model.status === "available" ? "success" : "outline"}
                              >
                                {MODEL_STATUS_LABELS[model.status] || model.status}
                              </Badge>
                              {model.is_default && <Badge variant="outline">默认</Badge>}
                              {model.status === "available"
                                && model.enabled
                                && (
                                  preference?.connection_id !== connection.connection_id
                                  || preference?.model_id !== model.model_id
                                ) && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`设 ${connection.display_name} 的 ${model.display_name} 为新任务默认`}
                                    onClick={() => void setUserDefault(connection, model)}
                                  >
                                    设为新任务默认
                                  </Button>
                                )}
                              {(connection.owner_scope === "user_personal" || isManager) && failed && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  disabled={isBusy}
                                  aria-label={`${model.status === "pending_validation" ? "验证" : "重试"} ${model.display_name}`}
                                  onClick={() => void retryModel(connection, model)}
                                >
                                  {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : model.status === "pending_validation" ? "验证（可能计费）" : "重试"}
                                </Button>
                              )}
                              {(connection.owner_scope === "user_personal" || isManager)
                                && model.status === "available"
                                && model.enabled
                                && !model.is_default && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`设 ${model.display_name} 为默认`}
                                    onClick={() => void changeDefaultModel(connection, model)}
                                  >
                                    设为默认
                                  </Button>
                                )}
                              {(connection.owner_scope === "user_personal" || isManager)
                                && model.status === "available"
                                && model.enabled && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`停用 ${model.display_name}`}
                                    onClick={() => void toggleModel(connection, model, false)}
                                  >
                                    停用
                                  </Button>
                                )}
                              {(connection.owner_scope === "user_personal" || isManager)
                                && model.status === "disabled" && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`启用 ${model.display_name}`}
                                    onClick={() => void toggleModel(connection, model, true)}
                                  >
                                    启用
                                  </Button>
                                )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                  {connection.owner_scope === "user_personal" && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openPersonalConnection(connection.preset_id)}
                    >
                      同 Provider 新建
                    </Button>
                  )}
                  {connection.owner_scope === "platform_shared" && isManager && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={modelAction === `connection:${connection.connection_id}`}
                      onClick={() =>
                        void togglePlatformConnection(
                          connection,
                          connection.status === "disabled",
                        )}
                    >
                      {connection.status === "disabled" ? "启用连接" : "停用连接"}
                    </Button>
                  )}
                  {connection.status === "pending_validation"
                    && (connection.owner_scope === "user_personal" || isManager) && (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={modelAction === `import:${connection.connection_id}`}
                        onClick={() => void verifyImported(connection)}
                      >
                        {modelAction === `import:${connection.connection_id}`
                          ? <Loader2 className="h-4 w-4 animate-spin" />
                          : "验证并启用（Key 无需重填）"}
                      </Button>
                    )}
                  {(connection.owner_scope === "user_personal" || isManager) && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => setDeleteTarget(connection)}
                    >
                      <Trash2 className="h-4 w-4" /> 删除
                    </Button>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}

      <Modal
        open={managedOpen}
        onClose={() => { if (!saving) { setManagedOpen(false); setManaged((current) => ({ ...current, api_key: "" })); } }}
        title={localService === "custom" ? "添加平台连接" : "连接本地模型（平台范围）"}
        wide
      >
        <fieldset disabled={saving} className="space-y-4">
        <div className="mb-4 grid grid-cols-2 gap-1 rounded-lg border bg-muted/30 p-1">
          <button
            type="button"
            aria-pressed={managedMode === "preset"}
            onClick={() => setManagedMode("preset")}
            className={cn(
              "rounded-md px-3 py-2 text-sm",
              managedMode === "preset"
                ? "bg-background font-medium shadow-sm"
                : "text-muted-foreground",
            )}
          >
            Provider 预设（推荐）
          </button>
          <button
            type="button"
            aria-pressed={managedMode === "custom"}
            data-model-tour="custom-lan"
            onClick={() => setManagedMode("custom")}
            className={cn(
              "rounded-md px-3 py-2 text-sm",
              managedMode === "custom"
                ? "bg-background font-medium shadow-sm"
                : "text-muted-foreground",
            )}
          >
            自定义 / LAN
          </button>
        </div>

        {managedMode === "preset" ? (
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="platform-preset-name" className="mb-1 block text-sm font-medium">
                连接名称 <span className="text-destructive">*</span>
              </label>
              <Input
                id="platform-preset-name"
                value={platformPreset.display_name}
                onChange={(event) =>
                  setPlatformPreset({
                    ...platformPreset,
                    display_name: event.target.value,
                  })}
                placeholder="例如：生产 DeepSeek"
              />
            </div>
            <div>
              <label htmlFor="platform-provider" className="mb-1 block text-sm font-medium">
                模型服务商 <span className="text-destructive">*</span>
              </label>
              <select
                id="platform-provider"
                value={selectedPlatformPreset?.preset_id || ""}
                onChange={(event) => {
                  const preset = presets.find(
                    (item) => item.preset_id === event.target.value,
                  );
                  setPlatformPreset({
                    ...platformPreset,
                    preset_id: event.target.value,
                    model: preset?.recommended_model || "",
                    api_key: "",
                  });
                  setPlatformRegion(preset?.regions?.[0]?.id || "");
                  setPlatformWorkspace("");
                }}
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                {presets.map((item) => (
                  <option key={item.preset_id} value={item.preset_id}>
                    {item.display_name}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Base URL 和 API 格式由平台预设，不需要技术配置。
              </p>
            </div>
            <div>
              <label htmlFor="platform-preset-model" className="mb-1 block text-sm font-medium">
                模型版本 <span className="text-destructive">*</span>
              </label>
              <select
                id="platform-preset-model"
                value={platformPreset.model}
                onChange={(event) =>
                  setPlatformPreset({
                    ...platformPreset,
                    model: event.target.value,
                  })}
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                {(selectedPlatformPreset?.models || []).map((model) => (
                  <option key={model} value={model}>
                    {selectedPlatformPreset?.model_catalog?.find(
                      (item) => item.model_id === model,
                    )?.display_name || model}
                    {model === selectedPlatformPreset?.recommended_model
                      ? "（平台推荐）"
                      : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="platform-preset-key" className="mb-1 block text-sm font-medium">
                API Key <span className="text-destructive">*</span>
              </label>
              <Input
                id="platform-preset-key"
                type="password"
                autoComplete="new-password"
                value={platformPreset.api_key}
                onChange={(event) =>
                  setPlatformPreset({
                    ...platformPreset,
                    api_key: event.target.value,
                  })}
                placeholder="必填，用于验证并发布平台共享连接"
              />
            </div>
            {!!selectedPlatformPreset?.regions?.length && <div className="space-y-2 md:col-span-2">
              <label htmlFor="platform-region" className="block text-sm font-medium">密钥所属地域</label>
              <select id="platform-region" value={platformRegion} onChange={(event) => { setPlatformRegion(event.target.value); setPlatformWorkspace(""); setPlatformPreset((current) => ({ ...current, api_key: "" })); }} className="h-10 w-full rounded-md border bg-background px-3 text-sm">
                {selectedPlatformPreset.regions.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
              {selectedPlatformPreset.regions.find((item) => item.id === platformRegion)?.workspace_required && <>
                <label htmlFor="platform-workspace" className="block text-sm font-medium">业务空间 ID</label>
                <Input id="platform-workspace" value={platformWorkspace} onChange={(event) => setPlatformWorkspace(event.target.value)} maxLength={63} placeholder="从百炼控制台复制" />
              </>}
            </div>}
            <p className="text-xs leading-5 text-muted-foreground md:col-span-2">
              {selectedPlatformPreset?.region_note} 保存时只验证所选模型，可能按服务商标准计费；不发送用户业务数据。
            </p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2 md:col-span-2">
              <label htmlFor="local-service" className="block text-sm font-medium">模型服务类型</label>
              <select id="local-service" value={localService} onChange={(event) => {
                const kind = event.target.value;
                setLocalService(kind); setDiscovery(null); setSetupError("");
                const port = kind === "ollama" ? "11434" : kind === "lmstudio" ? "1234" : "8000";
                setManaged({ display_name: kind === "ollama" ? "Ollama 本地模型" : kind === "lmstudio" ? "LM Studio 本地模型" : "本地模型", base_url: `http://localhost:${port}/v1`, api_format: "openai_chat_completions", model: "", model_ids_text: "", api_key: "" });
              }} className="h-10 w-full rounded-md border bg-background px-3 text-sm">
                <option value="ollama">Ollama</option><option value="lmstudio">LM Studio</option><option value="vllm">vLLM / OpenAI 兼容服务</option><option value="custom">其他接口（高级）</option>
              </select>
              <p className="text-xs leading-5 text-muted-foreground">先启动模型服务。示例地址适用于 Mangrove 与模型运行在同一电脑；服务器/容器部署需改为后端可达地址。只读取指定地址，不扫描设备。</p>
              <a className="text-xs text-primary hover:underline" href={localService === "ollama" ? "https://docs.ollama.com/api/openai-compatibility" : localService === "lmstudio" ? "https://lmstudio.ai/docs/developer/openai-compat" : "https://docs.vllm.ai/en/latest/serving/online_serving/"} target="_blank" rel="noreferrer">查看服务开启与接口指引</a>
            </div>
            <div>
              <label htmlFor="managed-name" className="mb-1 block text-sm font-medium">
                连接名称 <span className="text-destructive">*</span>
              </label>
              <Input id="managed-name" value={managed.display_name} onChange={(event) => setManaged({ ...managed, display_name: event.target.value })} />
            </div>
            <div>
              <label htmlFor="managed-url" className="mb-1 block text-sm font-medium">
                模型服务地址 <span className="text-destructive">*</span>
              </label>
              <Input id="managed-url" value={managed.base_url} onChange={(event) => { setManaged({ ...managed, base_url: event.target.value }); setDiscovery(null); }} placeholder="https://provider.example/v1 或精确 LAN 地址" />
            </div>
            <div>
              <label htmlFor="managed-format" className="mb-1 block text-sm font-medium">
                API 格式 <span className="text-destructive">*</span>
              </label>
              <select
                id="managed-format"
                value={managed.api_format}
                onChange={(event) => setManaged({ ...managed, api_format: event.target.value })}
                className="h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                {API_FORMATS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="managed-model" className="mb-1 block text-sm font-medium">
                {localService === "custom" ? "默认模型" : "本地模型 ID"} <span className="text-destructive">*</span>
              </label>
              <Input list="discovered-models" id="managed-model" value={managed.model} onChange={(event) => setManaged({ ...managed, model: event.target.value })} />
            </div>
            <datalist id="discovered-models">{discovery?.models.map((model) => <option key={model} value={model} />)}</datalist>
            {localService === "custom" && <div className="md:col-span-2">
              <label htmlFor="managed-models" className="mb-1 block text-sm font-medium">
                待验证模型 ID（每行一个，最多 8 个）
              </label>
              <textarea
                id="managed-models"
                value={managed.model_ids_text}
                onChange={(event) => setManaged({ ...managed, model_ids_text: event.target.value })}
                className="min-h-24 w-full rounded-md border bg-background px-3 py-2 text-sm"
                placeholder="可以先点击自动发现；发现失败时在这里手工输入"
              />
            </div>}
            <div>
              <label htmlFor="managed-key" className="mb-1 block text-sm font-medium">
                API Key
              </label>
              <Input id="managed-key" type="password" autoComplete="new-password" value={managed.api_key} onChange={(event) => setManaged({ ...managed, api_key: event.target.value })} />
              <p className="mt-1.5 text-xs text-muted-foreground">
                公网连接必须填写；无鉴权 LAN/本地服务可以留空。
              </p>
            </div>
            <p className="text-xs leading-5 text-amber-700 dark:text-amber-300 md:col-span-2">
              仅连接上方明确指定的地址。保存时发送所选模型的简短文本测试；高级多模型会逐项测试，可能产生用量。通过文本测试不代表工具任务资格。
            </p>
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving || !managed.base_url.trim()}
                onClick={() => void discoverManaged()}
              >
                {localService === "custom" ? "探测模型与四种协议（会产生测试用量）" : "读取可用模型"}
              </Button>
              {discovery && (
                <span className="text-xs text-muted-foreground">
                  {discovery.detected_api_formats.length
                    ? `已检测：${discovery.detected_api_formats.join("、")}`
                    : "仅取得列表，不代表文本或工具调用通过；可手填模型 ID"}
                </span>
              )}
            </div>
          </div>
        )}
        {setupError && <div role="alert" className="text-sm text-destructive">{setupError}</div>}
        </fieldset>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={saving} onClick={() => { setManagedOpen(false); setManaged((current) => ({ ...current, api_key: "" })); }}>取消</Button>
          <Button
            size="sm"
            disabled={
              saving || setupUnknown
              || (
                managedMode === "preset"
                  ? (
                    !platformPreset.display_name.trim()
                    || !platformPreset.model
                    || !platformPreset.api_key.trim()
                    || (!!selectedPlatformPreset?.regions?.find((item) => item.id === platformRegion)?.workspace_required && !platformWorkspace.trim())
                  )
                  : (
                    !managed.display_name.trim()
                    || !managed.base_url.trim()
                    || !managed.model.trim()
                  )
              )
            }
            onClick={
              managedMode === "preset"
                ? savePlatformPreset
                : saveManaged
            }
          >
            {saving ? "验证中…" : "验证并发布"}
          </Button>
        </div>
      </Modal>

      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="删除模型连接">
        <p className="text-sm text-muted-foreground">
          确定删除“{deleteTarget?.display_name}”吗？已冻结到历史任务版本的身份仍会保留，但此连接不能再签发新的使用权。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button variant="destructive" size="sm" onClick={deleteConnection}>确认删除</Button>
        </div>
      </Modal>
    </div>
  );
}
