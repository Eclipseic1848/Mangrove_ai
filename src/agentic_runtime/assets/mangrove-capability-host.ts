import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { readFileSync } from "node:fs";

const config = JSON.parse(
  readFileSync("/root/.pi/agent/capability-host.json", "utf8"),
) as {
  relayUrl: string;
  relayToken: string;
  capabilities: { name: string; kind: string }[];
};

function toolName(name: string): string {
  return `capability_${name.toLowerCase().replace(/[^a-z0-9_]+/g, "_")}`.slice(0, 64);
}

export default function mangroveCapabilityHost(pi: ExtensionAPI) {
  const names = new Map<string, string>();
  for (const capability of config.capabilities) {
    const name = toolName(capability.name);
    const existing = names.get(name);
    if (existing && existing !== capability.name) {
      throw new Error(`能力工具名称冲突：${existing} 与 ${capability.name}`);
    }
    names.set(name, capability.name);
    const mcp = capability.kind === "mcp_local";
    pi.registerTool({
      name,
      label: capability.name,
      description: "调用任务冻结的本地能力。能力运行在不挂载业务来源和模型凭证的隔离 Host 中。",
      parameters: mcp
        ? Type.Object({
            tool: Type.String(),
            arguments: Type.Optional(Type.Record(Type.String(), Type.Unknown())),
          })
        : Type.Object({ arguments: Type.Optional(Type.Array(Type.String())) }),
      async execute(_id, params, signal) {
        const response = await fetch(`${config.relayUrl}/invoke`, {
          method: "POST",
          headers: {
            authorization: `Bearer ${config.relayToken}`,
            "content-type": "application/json",
          },
          body: JSON.stringify(
            mcp
              ? { capability: capability.name, tool: params.tool, arguments: params.arguments ?? {} }
              : { capability: capability.name, arguments: params.arguments ?? [] },
          ),
          signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(String(result?.error ?? "能力调用失败"));
        return {
          content: [{ type: "text" as const, text: String(result.stdout ?? "") }],
          details: result,
        };
      },
    });
  }
}
