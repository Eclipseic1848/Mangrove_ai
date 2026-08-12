import { tool } from "@opencode-ai/plugin"
import { callTool } from "../tool_bridge"

export default tool({
  description: "读取一个已观察到的来源位置，并返回 EvidenceRef。",
  args: {
    source_id: tool.schema.string(),
    locator: tool.schema.string(),
  },
  async execute(args) {
    return JSON.stringify(await callTool("read_source", args))
  },
})
