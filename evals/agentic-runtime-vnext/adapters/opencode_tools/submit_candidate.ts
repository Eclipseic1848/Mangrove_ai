import { tool } from "@opencode-ai/plugin"
import { callTool } from "../tool_bridge"

export default tool({
  description: "提交一个候选文件；这不会发布正式交付。",
  args: {
    output_format: tool.schema.string(),
    filename: tool.schema.string(),
    content: tool.schema.string(),
  },
  async execute(args) {
    return JSON.stringify(await callTool("submit_candidate", args))
  },
})
