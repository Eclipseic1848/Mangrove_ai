import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readFileSync } from "node:fs";

type ToolConfig = {
  relayBaseUrl: string;
  grantToken: string;
  grantId: string;
  ownerBinding: string;
  taskId: string;
  revision: number;
  runId: string;
  purpose: string;
};

const config = JSON.parse(
  readFileSync("/root/.pi/agent/document-tools.json", "utf8"),
) as ToolConfig;

async function relay(operation: string, payload: unknown) {
  const response = await fetch(`${config.relayBaseUrl}/${operation}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${config.grantToken}`,
      "Content-Type": "application/json",
      "X-Mangrove-Grant-ID": config.grantId,
      "X-Mangrove-Owner-Binding": config.ownerBinding,
      "X-Mangrove-Task-ID": config.taskId,
      "X-Mangrove-Revision": String(config.revision),
      "X-Mangrove-Run-ID": config.runId,
      "X-Mangrove-Purpose": config.purpose,
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(120_000),
  });
  const body = await response.text();
  if (!response.ok) {
    throw new Error(`Mangrove 文档工具 ${operation} 失败（HTTP ${response.status}）：${body.slice(0, 500)}`);
  }
  return JSON.parse(body) as Record<string, unknown>;
}

function result(value: Record<string, unknown>) {
  return {
    // Pi 的官方 truncateHead 只保留完整行；单行大 JSON 会把首行整体丢弃，
    // 进而让模型看不到 evidence_ref。格式化后可稳定保留结构头和结尾证据。
    content: [{ type: "text" as const, text: JSON.stringify(value, null, 2) }],
    details: value,
  };
}

export default function mangroveDocumentTools(pi: ExtensionAPI) {
  pi.registerTool({
    name: "request_clarification",
    label: "请求一个澄清",
    description: "仅当范围或结果数量的歧义会实质改变结果时，向用户提出一个简短问题并停止；不得在覆盖契约冻结后调用。",
    parameters: Type.Object({
      question: Type.String(),
      reason: Type.String(),
    }),
    async execute(_toolCallId, params) {
      return result(await relay("request_clarification", params));
    },
  });

  pi.registerTool({
    name: "inspect_source",
    label: "检查来源结构",
    description: "检查一个获准来源的页数、页型和可用能力，不读取整份高质量 OCR。首次调用使用 goal.json source_scope 中的 /workspace/input/... 路径；返回的 source_id 是后续文档工具使用的规范标识。",
    parameters: Type.Object({ source_id: Type.String() }),
    async execute(_toolCallId, params) {
      return result(await relay("inspect_source", params));
    },
  });

  pi.registerTool({
    name: "freeze_coverage",
    label: "冻结覆盖理解",
    description: "在读取证据前提交对用户范围、顶层结果对象数量、完整性和停止条件的结构化理解。第 N 个对象使用 ordinal；对象内部包含多少行、人员或字段不改变顶层基数。该契约只能冻结一次。",
    parameters: Type.Object({
      authorized_scope: Type.Object({
        source_ids: Type.Array(Type.String()),
        unit_ids: Type.Optional(Type.Array(Type.String())),
      }),
      result_cardinality: Type.Union([
        Type.Literal("first"),
        Type.Literal("ordinal"),
        Type.Literal("count"),
        Type.Literal("all"),
      ]),
      result_count: Type.Optional(Type.Integer({ minimum: 1 })),
      result_ordinal: Type.Optional(Type.Integer({ minimum: 1 })),
      completeness: Type.Union([
        Type.Literal("strict"),
        Type.Literal("best_effort"),
      ]),
      ordering: Type.String(),
      required_fields: Type.Array(Type.String()),
      object_boundary: Type.String(),
      stop_semantics: Type.String(),
      interpretation: Type.String(),
      confidence: Type.Union([Type.Literal("high"), Type.Literal("low")]),
    }),
    async execute(_toolCallId, params) {
      return result(await relay("freeze_coverage", params));
    },
  });

  pi.registerTool({
    name: "discover_content",
    label: "发现候选内容",
    description: "在冻结范围内寻找候选内容单元。发现文本只用于召回，不能当作最终证据。",
    parameters: Type.Object({
      source_id: Type.String(),
      query: Type.String(),
      unit_ids: Type.Optional(Type.Array(Type.String())),
    }),
    async execute(_toolCallId, params) {
      return result(await relay("discover_content", params));
    },
  });

  pi.registerTool({
    name: "read_evidence",
    label: "权威读取证据",
    description: "精读冻结范围内的候选页，返回可复核文本、坐标、解析版本和稳定证据引用。",
    parameters: Type.Object({
      source_id: Type.String(),
      unit_ids: Type.Array(Type.String()),
      needs: Type.Optional(Type.Array(Type.String())),
    }),
    async execute(_toolCallId, params) {
      return result(await relay("read_evidence", params));
    },
  });

  pi.registerTool({
    name: "propose_completion",
    label: "提议完成",
    description: "提交停止提议和可验证证明。所有 evidence_refs 字段都必须填写 read_evidence 返回的 evidence_ref（例如 evidence:source:page:1），不得填写证据原文。独立完成门可能通过，也可能返回需要继续处理的缺口。",
    parameters: Type.Object({
      summary: Type.String(),
      ordering_proof: Type.Optional(Type.Array(Type.String())),
      results: Type.Array(Type.Object({
        result_id: Type.String(),
        unit_ids: Type.Array(Type.String(), { minItems: 1 }),
        evidence_refs: Type.Array(Type.String({ description: "read_evidence 返回的 evidence_ref，不是原文" }), { minItems: 1 }),
        boundary_evidence_refs: Type.Array(Type.String({
          description: "证明对象边界的 evidence_ref，必须属于当前结果的 unit_ids，不得填写相邻对象页面",
        }), { minItems: 1 }),
        required_field_evidence: Type.Record(
          Type.String(),
          Type.Array(Type.String({ description: "证明该字段的 evidence_ref，不是字段值或原文" }), { minItems: 1 }),
        ),
      })),
      rejected_candidates: Type.Optional(Type.Array(Type.Object({
        unit_id: Type.String({ description: "经权威读取后确认不是结果的候选内容单元" }),
        evidence_refs: Type.Array(Type.String({ description: "支持排除判断的 read_evidence evidence_ref" }), { minItems: 1 }),
      }))),
      result_empty_confirmed: Type.Optional(Type.Boolean()),
    }),
    async execute(_toolCallId, params) {
      return result(await relay("propose_completion", params));
    },
  });
}
