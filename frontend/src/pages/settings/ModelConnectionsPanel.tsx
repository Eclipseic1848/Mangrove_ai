import { useEffect, useMemo, useRef, useState } from "react";
import { EVENTS, Joyride, type Step } from "react-joyride";
import {
  SiAlibabacloud,
  SiAnthropic,
  SiGooglegemini,
  SiOpenai,
} from "@icons-pack/react-simple-icons";
import {
  KeyRound,
  LifeBuoy,
  Loader2,
  Plus,
  Server,
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
import { Link } from "react-router-dom";
import { TaskModelSettings } from "./TaskModelSettings";
import { ConnectionEditor } from "./ConnectionEditor";
import { modelConnectionSource, taskModelChoices } from "@/lib/taskModelChoices";

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
  current_catalog?: boolean;
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

export function ModelConnectionsPanel({ isManager, initialScope = "platform" }: { isManager: boolean; initialScope?: "personal" | "platform" }) {
  const { theme } = useTheme();
  const [presets, setPresets] = useState<ProviderPreset[]>([]);
  const [connections, setConnections] = useState<ModelConnection[]>([]);
  const [configuredLocalModels, setConfiguredLocalModels] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [scope, setScope] = useState<"personal" | "platform">(initialScope);
  const [showSetup, setShowSetup] = useState(false);
  const [personalName, setPersonalName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState("");
  const [workspaceId, setWorkspaceId] = useState("");
  const [setupError, setSetupError] = useState("");
  const [setupUnknown, setSetupUnknown] = useState(false);
  const [recoveryChecked, setRecoveryChecked] = useState(false);
  const submittedScope = useRef<"personal" | "platform">("personal");
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
  const [manageModel, setManageModel] = useState<{ model: string; connectionId: string } | null>(null);
  const [managedRecord, setManagedRecord] = useState("");
  const [managedMode, setManagedMode] = useState<"preset" | "custom">("preset");
  const [localService, setLocalService] = useState("custom");
  const [platformRegion, setPlatformRegion] = useState("");
  const [platformWorkspace, setPlatformWorkspace] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<ModelConnection | null>(null);
  const [disableTarget, setDisableTarget] = useState<ModelConnection | null>(null);
  const mutation = useRef(false);
  const [mutating, setMutating] = useState(false);
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
  const platformConnections = connections.filter(item => item.owner_scope === "platform_shared").map(connection => ({
    ...connection,
    // 展示模型/供应商名称，不向用户暴露导入来源等内部命名；连接身份不变。
    display_name: ["managed_private", "local"].includes(connection.locality)
      ? connection.models?.find(item => item.model_id === connection.model)?.display_name || connection.model
      : presets.find(item => item.preset_id === connection.preset_id)?.display_name || connection.preset_id || "自定义云端服务",
  }));
  // 去重使用原始供应商身份；简化展示名称不能合并不同自定义供应商。
  // 旧直连导入记录跟随现有本地目录退役；用户新建连接不按目录裁剪，失败型号仍可修复。
  const currentPlatformConnections = connections.filter(item => item.owner_scope === "platform_shared").map(connection => ({ ...connection,
    models: connection.models?.filter(model => model.current_catalog !== false && !(configuredLocalModels !== null
      && ["managed_private", "local"].includes(connection.locality) && model.catalog_role === "legacy_imported"
      && !configuredLocalModels.some(name => name.toLowerCase() === model.model_id.toLowerCase()))) }));
  const platformChoices = taskModelChoices([], currentPlatformConnections, false, undefined,
    { connectionId: preference?.connection_id ?? null, model: preference?.model_id ?? null }, isManager)
    .map(choice => ({ ...choice, supplier: choice.supplier.replace(/^导入的?(?:平台)?[ ·]*/, ""), label: choice.label.replace(/^导入的?(?:平台)?[ ·]*/, "") }));

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [presetResult, connectionResult, preferenceResult, localResult] =
        await Promise.allSettled([
        api.get("/api/model-connections/presets"),
        api.get("/api/model-connections"),
        api.get("/api/model-connections/preferences/default"),
        isManager ? api.get("/api/config/models") : Promise.resolve(null),
      ]);
      const errors: string[] = [];
      if (localResult.status === "fulfilled" && localResult.value) setConfiguredLocalModels(localResult.value.models?.local ?? []);
      if (localResult.status === "rejected") errors.push("本地模型目录加载失败");
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
      if (preferenceResult.status === "fulfilled") {
        setPreference(preferenceResult.value.preference ?? null);
      }
      const nextPresets = (presetData.items || []) as ProviderPreset[];
      const nextConnections = (connectionData.items || []) as ModelConnection[];
      setPresets(nextPresets);
      setConnections(nextConnections);
      setRefreshToken(current => current + 1);
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
      return errors.length === 0;
    } catch (error: any) {
      const message = error.message || "模型连接加载失败";
      setLoadError(message);
      setShowSetup(true);
      toast.error(message);
      return false;
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
    submittedScope.current = "personal";
    setRecoveryChecked(false);
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
    submittedScope.current = "platform";
    setRecoveryChecked(false);
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
    if (submission.current || setupUnknown) return;
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
    submittedScope.current = "platform";
    setRecoveryChecked(false);
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
        // 保存后给出下一步，但不自动切换个人默认模型。
        display_name: "",
        preset_id: selectedPlatformPreset.preset_id,
        model: selectedPlatformPreset.recommended_model,
        api_key: "",
      });
      setSavedConnection(saved);
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
    if (!setupUnknown) setSetupError("");
    if (preset) {
      setPlatformRegion(preset.regions?.[0]?.id || "");
      setPlatformWorkspace("");
      setPlatformPreset((current) => ({
        ...current,
        preset_id: preset.preset_id,
        model: current.model || preset.recommended_model,
        display_name: `${preset.display_name} 平台连接`,
        api_key: "",
      }));
    }
    setManagedOpen(true);
  };

  const deleteConnection = async () => {
    if (!deleteTarget || mutation.current) return;
    mutation.current = true;
    setMutating(true);
    setSavedConnection(null);
    try {
      await api.del(`/api/model-connections/${deleteTarget.connection_id}`);
      toast.success("连接已删除");
      setDeleteTarget(null);
      setManageModel(null);
      await load();
    } catch (error: any) {
      toast.error(error.message || "删除失败");
    } finally {
      mutation.current = false;
      setMutating(false);
    }
  };

  const retryModel = async (
    connection: ModelConnection,
    model: ConnectionModel,
  ) => {
    const action = `retry:${connection.connection_id}:${model.model_id}`;
    setModelAction(action);
    try {
      const result = await api.post(
        `/api/model-connections/${connection.connection_id}/models/retry`,
        { model_ids: [model.model_id] },
      );
      if (result.models?.some((item: ConnectionModel) => item.model_id === model.model_id && item.status === "available")) toast.success(`${model.display_name} 验证通过`);
      else toast.info("验证已结束，请查看模型状态；未通过的模型不会启用。");
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
      toast.info("验证已结束，请查看模型状态；无需重新填写 Key。");
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
    if (mutation.current || (!enabled && !window.confirm(`停用“${model.display_name}”后，新任务不能再选择它。若它是连接首选模型，还需重新选择连接首选模型。确认停用？`))) return;
    mutation.current = true;
    const action = `toggle:${connection.connection_id}:${model.model_id}`;
    setSavedConnection(null);
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
      mutation.current = false;
      setModelAction("");
    }
  };

  const togglePlatformConnection = async (
    connection: ModelConnection,
    enabled: boolean,
  ) => {
    if (mutation.current) return;
    mutation.current = true;
    setMutating(true);
    const action = `connection:${connection.connection_id}`;
    setSavedConnection(null);
    setModelAction(action);
    try {
      await api.patch(`/api/model-connections/${connection.connection_id}`, { enabled });
      toast.success(`平台连接已${enabled ? "启用" : "停用"}`);
      setDisableTarget(null);
      await load();
    } catch (error: any) {
      toast.error(error.message || "平台连接状态修改失败");
    } finally {
      mutation.current = false;
      setMutating(false);
      setModelAction("");
    }
  };

  const setUserDefault = async (
    connection: ModelConnection,
    model: ConnectionModel,
  ) => {
    if (connection.status !== "verified" || model.status !== "available" || !model.enabled || model.current_catalog === false) return;
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
    setScope("personal");
    const preset = presets.find((item) => item.preset_id === presetId)
      ?? selectedPreset
      ?? presets[0];
    if (preset) {
      setSelectedPresetId(preset.preset_id);
      setSelectedModel(preset.recommended_model);
      setPersonalName(suggestedPersonalName(preset, connections));
    }
    setApiKey("");
    if (!setupUnknown) setSetupError("");
    setShowSetup(true);
  };

  const checkSavedResult = async () => {
    setManagedOpen(false); setShowSetup(false); setScope(submittedScope.current);
    setApiKey("");
    setManaged(current => ({ ...current, api_key: "" }));
    setPlatformPreset(current => ({ ...current, api_key: "" }));
    setRecoveryChecked(await load());
  };

  const updateManagedEndpoint = (field: "base_url" | "api_key", value: string) => {
    // 旧服务发现值失效，但保留用户手填内容，交给新服务验证。
    setManaged({ ...managed, [field]: value,
      model: discovery?.models.includes(managed.model) ? "" : managed.model,
      model_ids_text: discovery && managed.model_ids_text === discovery.models.join("\n") ? "" : managed.model_ids_text });
    setDiscovery(null);
  };

  const activeConnection = connections.find(item => item.connection_id === manageModel?.connectionId);
  // 同型号合并展示，但维护命令仍绑定真实连接，不把不同账户或供应商合并删除。
  const relatedConnections = platformConnections.filter(item => {
    const raw = currentPlatformConnections.find(current => current.connection_id === item.connection_id);
    return activeConnection && raw && JSON.stringify(modelConnectionSource(raw)) === JSON.stringify(modelConnectionSource(activeConnection))
      && raw.models?.some(model => model.model_id.trim().toLowerCase() === manageModel?.model.trim().toLowerCase());
  });
  const visibleConnections = scope === "platform"
    ? relatedConnections.filter(item => item.connection_id === managedRecord).map(connection => ({ ...connection, models: currentPlatformConnections.find(item => item.connection_id === connection.connection_id)?.models }))
    : personalConnections;

  const maintenanceContent = (
          <CardContent className={cn("space-y-2", scope === "platform" && "p-0")}>
            {loading ? (
              <p className="py-8 text-center text-sm text-muted-foreground">加载中…</p>
            ) : loadError && visibleConnections.length === 0 ? null : visibleConnections.length === 0 ? (
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
                  {scope === "personal" && <ProviderMark id={connection.preset_id || "managed"} />}
                  <div className={cn("min-w-0", scope === "platform" ? "w-full" : "flex-1")}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{connection.display_name}</span>
                      {scope === "platform" && <span className="text-xs text-muted-foreground">连接编号 {connection.connection_id.slice(0, 8)}</span>}
                      <Badge variant="outline" className={connection.status === "verified" ? "border-emerald-600/30 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" : ""}>
                        {connection.status === "verified"
                          ? "连接已验证"
                          : connection.status === "needs_default_model"
                            ? "需要恢复连接"
                            : connection.status === "disabled"
                              ? "连接已停用"
                              : MODEL_STATUS_LABELS[connection.status] || "需要检查"}
                      </Badge>
                      <Badge variant="outline">
                        {connection.owner_scope === "user_personal" ? "仅自己可用" : "平台共享"}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      {connection.status === "disabled" ? "当前不可用于新任务" : (connection.models?.length ?? 0) > 0
                        ? `${connection.available_model_count ?? 0} / ${connection.models?.length} 个模型可用`
                        : connection.model}
                      {(connection.owner_scope === "user_personal" || isManager) && connection.default_model
                        ? ` · 连接首选 ${
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
                          const isBusy = !!modelAction || mutating;
                          const available = connection.status === "verified" && model.status === "available" && model.enabled && model.current_catalog !== false;
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
                              <span className="w-full min-w-0 break-words text-sm font-medium sm:w-auto sm:flex-1">
                                {model.display_name}
                              </span>
                              <Badge
                                variant="outline"
                                className={available ? "border-emerald-600/30 bg-emerald-50 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200" : ""}
                              >
                                {connection.status === "disabled" ? "随连接停用" : model.current_catalog === false ? "不用于新任务" : !available && model.status === "available" ? "连接需恢复" : MODEL_STATUS_LABELS[model.status] || "需要检查"}
                              </Badge>
                              {preference?.connection_id === connection.connection_id && preference.model_id === model.model_id && <Badge variant="outline">我的任务默认</Badge>}
                              {scope === "personal" && available
                                && (
                                  preference?.connection_id !== connection.connection_id
                                  || preference?.model_id !== model.model_id
                                ) && (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`设 ${connection.display_name} 的 ${model.display_name} 为新任务默认`}
                                    onClick={() => void setUserDefault(connection, model)}
                                  >
                                    设为我的默认模型
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
                                && connection.status === "needs_default_model" && !model.is_default && (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={isBusy}
                                    aria-label={`设 ${model.display_name} 为连接首选模型`}
                                    onClick={() => void changeDefaultModel(connection, model)}
                                  >
                                    设为连接首选并恢复
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
                  {(connection.owner_scope === "user_personal" || isManager) && connection.status === "verified" && (connection.models?.length ?? 0) > 1 && <details className="w-full text-sm">
                    <summary className="cursor-pointer py-2 focus-visible:ring-2 focus-visible:ring-ring">连接高级设置 · {connection.display_name}</summary>
                    <label htmlFor={`connection-default-${connection.connection_id}`} className="mb-1 block">连接首选模型</label>
                    <select id={`connection-default-${connection.connection_id}`} value={connection.default_model || connection.model} disabled={!!modelAction || mutating}
                      className="h-10 max-w-full rounded-md border bg-background px-3"
                      onChange={event => { const model = connection.models?.find(item => item.model_id === event.target.value); if (model) void changeDefaultModel(connection, model); }}>
                      {connection.models?.filter(item => item.status === "available" && item.enabled).map(item => <option key={item.model_id} value={item.model_id}>{item.display_name}</option>)}
                    </select>
                    <p className="mt-2 text-xs text-muted-foreground">用于维护这套连接，不会更改“我的任务默认模型”。停用当前首选前，可先在这里切换。</p>
                  </details>}
                  {(connection.owner_scope === "user_personal" || isManager) && <ConnectionEditor connectionId={connection.connection_id} onSaved={() => { setManageModel(null); void load(); toast.success("配置已保存，新任务可选择新版本；历史任务保持原配置"); }} />}
                  {connection.owner_scope === "user_personal" && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openPersonalConnection(connection.preset_id)}
                    >
                      添加同服务商账号
                    </Button>
                  )}
                  {connection.owner_scope === "platform_shared" && isManager && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={modelAction === `connection:${connection.connection_id}`}
                      onClick={() =>
                        connection.status === "disabled"
                          ? void togglePlatformConnection(connection, true)
                          : setDisableTarget(connection)}
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
  );

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
            平台模型可直接使用，无需填写密钥。也可以添加自己的模型，仅供自己的任务使用。
          </p>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer rounded-md px-3 py-2 focus-visible:ring-2 focus-visible:ring-ring">帮助与高级操作</summary>
          <div className="mt-2 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => void importLegacy()}>
            导入现有配置
          </Button>
          <Button variant="outline" size="sm" onClick={startTour} className="gap-1.5">
            <LifeBuoy className="h-4 w-4" /> 播放新手引导
          </Button>
          </div>
        </details>
      </div>

      <div className={cn("flex flex-wrap gap-2", scope === "personal" && showSetup && "hidden")} aria-label="模型接入方式">
        <Button variant="outline" disabled={saving} onClick={() => { setLocalInfo(false); openPersonalConnection(); }}>添加自己的模型</Button>
        <Button variant="ghost" disabled={saving} onClick={() => setLocalInfo(!localInfo)}>如何接入本地模型</Button>
      </div>
      {localInfo && <div className="space-y-3 rounded-lg border p-4 text-sm">
        <h3 className="font-semibold">连接已经运行的本地模型</h3>
        {isManager && <p>支持 Ollama、LM Studio、vLLM 等服务。先启动模型服务，再填写 Mangrove 后端可以访问的地址；localhost 指后端所在电脑，不会自动扫描内网。</p>}
        <p>{isManager ? "登记后获准用户可使用。只测试明确选定的模型；无鉴权服务无需密钥。" : "平台本地模型可直接使用。自己的本地服务需要管理员登记：请提供服务类型、地址和模型名称，不要通过聊天发送密钥。"}</p>
        {isManager && <Button onClick={() => { setLocalService("ollama"); setManagedMode("custom"); setManaged({ display_name: "Ollama 本地模型", base_url: "http://localhost:11434/v1", api_format: "openai_chat_completions", model: "", model_ids_text: "", api_key: "" }); setDiscovery(null); if (!setupUnknown) setSetupError(""); setManagedOpen(true); }}>登记本地模型</Button>}
        <Button variant="ghost" onClick={() => { setScope("platform"); setShowSetup(false); setLocalInfo(false); }}>查看平台可用连接</Button>
      </div>}
      {savedConnection && <div role="status" className="space-y-2 rounded-lg border border-primary/30 p-4 text-sm">
        <p>已保存：{savedConnection.display_name} · {savedConnection.default_model || savedConnection.model}</p>
        <p>可用状态请以下方模型列表为准。连接测试不等于完整任务效果验证；其他模型不会自动启用。</p>
        <Link to="/data-prep" className="inline-flex min-h-10 items-center rounded-md bg-primary px-4 text-primary-foreground">去工作台</Link>
        <Button variant="outline" disabled={!!modelAction || savedConnection.status !== "verified" || !savedConnection.models?.some(item => item.is_default && item.enabled && item.status === "available" && item.current_catalog !== false)} onClick={() => { const model = savedConnection.models?.find((item) => item.is_default); if (model) void setUserDefault(savedConnection, model); }}>设为我的默认模型</Button>
      </div>}

      <div data-model-tour="default-connection" hidden={scope === "personal" && showSetup && !tourRun}>
        <TaskModelSettings refreshToken={refreshToken} onSaved={() => void load()} />
      </div>

      {setupUnknown && !managedOpen && <div role="alert" className="space-y-3 rounded-lg border border-amber-400 bg-amber-50 p-4 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
        <p>上次保存结果尚未确认。请先核对下方连接列表和服务商用量记录，避免重复保存或计费。</p>
        <Button variant="outline" disabled={loading} onClick={() => void checkSavedResult()}>检查保存结果</Button>
        {recoveryChecked && <>
          <p>列表已刷新，但不能据此确认服务商未计费。如果已有连接，请直接使用；只有仍需创建时才重新测试。</p>
          <Button variant="outline" disabled={loading || !!loadError} onClick={() => {
            if (window.confirm("重新测试可能创建重复连接并再次计费。确认已检查连接列表和服务商记录，仍需重新测试？")) {
              setSetupUnknown(false); setSetupError(""); setRecoveryChecked(false);
            }
          }}>我已核对，允许重新测试</Button>
        </>}
      </div>}

      {
        <div role="tablist" aria-label="模型连接范围" className="inline-flex rounded-lg border bg-muted/30 p-1" onKeyDown={event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? "personal" : event.key === "End" ? "platform" : scope === "personal" ? "platform" : "personal";
          setScope(next); setShowSetup(false);
          document.getElementById(`model-scope-${next}`)?.focus();
        }}>
          <button
            type="button"
            role="tab"
            data-model-tour="platform-connections"
            id="model-scope-personal"
            aria-controls="model-scope-content"
            tabIndex={scope === "personal" ? 0 : -1}
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
            id="model-scope-platform"
            aria-controls="model-scope-content"
            tabIndex={scope === "platform" ? 0 : -1}
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

      {loadError && <div role="alert" className="space-y-2 rounded-lg border border-destructive/30 p-4 text-sm">
        <p>模型连接加载失败：{loadError}</p>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => void load()}>重新加载</Button>
      </div>}

      <div id="model-scope-content" role="tabpanel" aria-labelledby={`model-scope-${scope}`}>
      {scope === "personal" && showSetup ? (
        <div className="max-w-3xl">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold">连接一个模型服务</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    选择服务商，按官方指引获取密钥，再选择并测试一个模型。
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setShowSetup(false)}>返回列表</Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset disabled={saving} className="space-y-5">
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
              {setupError && !managedOpen && !setupUnknown && <div role="alert" className="space-y-2 text-sm text-destructive">
                <p>{setupError}</p>
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
                    </div>
                    <div className="mt-1.5 flex justify-between gap-3 text-xs text-muted-foreground">
                      <span>保存后只显示尾部遮罩，不提供复制或导出。</span>
                      <a href={selectedPreset.key_url || selectedPreset.help_url} target="_blank" rel="noreferrer" className="shrink-0 text-primary hover:underline">
                        获取 Key
                      </a>
                    </div>
                  </div>
                  <details className="text-sm">
                    <summary className="cursor-pointer py-2 focus-visible:ring-2 focus-visible:ring-ring">连接名称（已自动填写，可选修改）</summary>
                    <label htmlFor="personal-connection-name" className="mb-1 block">连接名称</label>
                    <Input id="personal-connection-name" value={personalName} onChange={event => setPersonalName(event.target.value)} maxLength={80} />
                  </details>
                  <p data-model-tour="verify" className="rounded-md bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">仅发送简短测试，可能按服务商标准计费；不发送任务正文或附件。测试只检查接口连通，不保证任务效果。</p>
                  <Button
                    data-model-tour="complete"
                    disabled={!personalName.trim() || !apiKey.trim() || saving || setupUnknown || (!!selectedPreset.regions?.find((item) => item.id === region)?.workspace_required && !workspaceId.trim())}
                    onClick={savePersonal}
                    className="w-full"
                  >
                    {saving
                      ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" />正在测试并保存…</>
                      : "测试并保存所选模型"}
                  </Button>
                </>
              )}
              </fieldset>
            </CardContent>
          </Card>

        </div>
      ) : (
        <Card id="model-connection-list" tabIndex={-1}>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle className="text-base">
                  {scope === "platform" ? "平台模型" : "可用连接"}
                </CardTitle>
                <p className="mt-1 text-xs text-muted-foreground">{scope === "platform" ? `${platformChoices.length} 个${isManager ? "已配置型号 · 点击管理可启停或排查" : "可用模型"}` : `${visibleConnections.length} 个连接`}</p>
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
          {scope === "platform" && <CardContent>
            <section aria-label="平台模型清单" className="space-y-5">
              {loading ? <p role="status">正在加载模型…</p> : !loadError && ["本地模型", "云端模型"].map(group => {
                const choices = platformChoices.filter(item => item.group === group);
                return <section key={group} aria-label={group} className="space-y-3">
                  <h3 className="text-base font-semibold">{group}</h3>
                  {!choices.length && <p className="text-sm text-muted-foreground">暂无可用{group}</p>}
                  {Array.from(new Set(choices.map(item => item.supplier))).map(supplier => <div key={supplier} className="space-y-2">
                    {supplier && <h4 className="text-sm font-medium text-muted-foreground">{supplier}</h4>}
                    {choices.filter(item => item.supplier === supplier).map(choice => {
                      const connection = platformConnections.find(item => item.connection_id === choice.connectionId);
                      const model = connection?.models?.find(item => item.model_id === choice.model);
                      const available = connection?.status === "verified" && model?.enabled && model.status === "available";
                      return <div key={`${choice.connectionId}:${choice.model}`} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-3">
                      <span className="text-sm font-medium">{model?.display_name || choice.model}</span>
                      <div className="flex flex-wrap items-center gap-2">
                      {isManager && !available && <Badge variant="outline">{connection?.status === "disabled" ? "已停用" : MODEL_STATUS_LABELS[model?.status || ""] || "需检查"}</Badge>}
                      {isManager && <Button variant="outline" size="sm" aria-label={`管理 ${choice.label}`} onClick={() => { setManageModel({ model: choice.model, connectionId: choice.connectionId }); setManagedRecord(choice.connectionId); }}>管理</Button>}
                      {preference?.connection_id === choice.connectionId && preference.model_id === choice.model
                        ? <Badge variant="outline">我的任务默认</Badge>
                        : <Button variant="outline" size="sm" disabled={!!modelAction || mutating || !available} aria-label={`设 ${choice.label} 为新任务默认`} onClick={() => {
                          if (connection && model) void setUserDefault(connection, model);
                        }}>设为我的默认模型</Button>}
                      </div>
                    </div>; })}
                  </div>)}
                </section>;
              })}
            </section>
          </CardContent>}
          {scope === "personal" && maintenanceContent}
          {scope === "platform" && isManager && <Modal open={!!manageModel} onClose={() => setManageModel(null)} title="管理平台模型" wide>
            {relatedConnections.length > 1 && <div className="mb-4">
              <label htmlFor="managed-record" className="mb-2 block text-sm">使用的连接记录</label>
              <select id="managed-record" value={managedRecord} onChange={event => setManagedRecord(event.target.value)} className="h-10 w-full rounded-md border bg-background px-3">
                {relatedConnections.map(item => <option key={item.connection_id} value={item.connection_id}>{item.status === "verified" ? "已验证" : MODEL_STATUS_LABELS[item.status] || "需检查"} · {item.connection_id.slice(0, 8)}</option>)}
              </select>
              <p className="mt-2 text-xs text-muted-foreground">同型号存在多套配置；只维护所选记录，不会替换历史任务或个人默认模型。</p>
            </div>}
            {maintenanceContent}
            <Button variant="outline" className="mt-4" onClick={() => setManageModel(null)}>完成管理</Button>
          </Modal>}
        </Card>
      )}

      </div>
      <Modal
        open={managedOpen}
        onClose={() => { if (!saving) { setManagedOpen(false); setManaged((current) => ({ ...current, api_key: "" })); } }}
        title={localService === "custom" ? "添加平台连接" : "连接本地模型（平台范围）"}
        wide
      >
        <p className="mb-4 text-sm text-muted-foreground">保存后向获准的平台用户共享，密钥不会公开。仅测试选定模型，不会修改个人任务默认模型。</p>
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
            云端服务商
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
            本地或自定义服务
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
                    display_name: !platformPreset.display_name || platformPreset.display_name === `${selectedPlatformPreset?.display_name} 平台连接` ? `${preset?.display_name || "模型"} 平台连接` : platformPreset.display_name,
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
                服务地址与接口已自动填写，无需技术配置。
              </p>
            </div>
            <div>
              <label htmlFor="platform-preset-model" className="mb-1 block text-sm font-medium">
                选择模型 <span className="text-destructive">*</span>
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
              <Input id="managed-url" value={managed.base_url} onChange={event => updateManagedEndpoint("base_url", event.target.value)} placeholder="https://provider.example/v1 或精确局域网地址" />
              <p className="mt-1 text-xs text-muted-foreground">更换地址或密钥后，旧发现结果会清除；手填模型保留，保存时重新验证。</p>
            </div>
            <div>
              <label htmlFor="managed-key" className="mb-1 block text-sm font-medium">
                API Key
              </label>
              <Input id="managed-key" type="password" autoComplete="new-password" value={managed.api_key} onChange={event => updateManagedEndpoint("api_key", event.target.value)} />
              <p className="mt-1.5 text-xs text-muted-foreground">
                公网连接必须填写；无鉴权 LAN/本地服务可以留空。
              </p>
            </div>
            <p className="text-xs leading-5 text-amber-700 dark:text-amber-300 md:col-span-2">
              仅连接上方明确指定的地址。保存时发送所选模型的简短请求；高级多模型会逐项测试，可能产生用量。这里只验证接口连通，完整文本生成和工具能力需另外验证。
            </p>
            <div className="flex flex-wrap items-center gap-2 md:col-span-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving || setupUnknown || !managed.base_url.trim()}
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
            {localService === "custom" && <div>
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
            </div>}
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
          </div>
        )}
        {setupError && <div role="alert" className="text-sm text-destructive">{setupError}</div>}
        {setupUnknown && <div className="space-y-2 text-sm"><p>上次保存结果未确认，已暂停保存和探测，请先核对。</p><Button variant="outline" onClick={() => void checkSavedResult()}>检查保存结果</Button></div>}
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
            {saving ? "正在测试并保存…" : "测试并共享"}
          </Button>
        </div>
      </Modal>

      <Modal open={!!disableTarget} onClose={() => { if (!mutating) setDisableTarget(null); }} title="停用平台连接">
        <p className="text-sm">停用“{disableTarget?.display_name}”会撤销此连接的使用授权，影响所有使用者，也可能中断正在执行的任务。历史任务记录仍保留。</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" disabled={mutating} onClick={() => setDisableTarget(null)}>取消</Button>
          <Button variant="destructive" disabled={mutating} onClick={() => { if (disableTarget) void togglePlatformConnection(disableTarget, false); }}>{mutating ? "正在停用…" : "确认停用"}</Button>
        </div>
      </Modal>
      <Modal open={!!deleteTarget} onClose={() => { if (!mutating) setDeleteTarget(null); }} title="删除模型连接">
        <p className="text-sm text-muted-foreground">
          确定删除“{deleteTarget?.display_name}”吗？已冻结到历史任务版本的身份仍会保留，但此连接不能再签发新的使用权。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" disabled={mutating} onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button variant="destructive" size="sm" disabled={mutating} onClick={deleteConnection}>{mutating ? "正在删除…" : "确认删除"}</Button>
        </div>
      </Modal>
    </div>
  );
}
