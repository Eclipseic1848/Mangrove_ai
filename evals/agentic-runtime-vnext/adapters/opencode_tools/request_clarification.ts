import { tool } from "@opencode-ai/plugin"
import { callTool } from "../tool_bridge"

export default tool({
  description: "提出一个最小必要问题，并给出 2 至 4 个可执行的真实操作。",
  args: {
    question: tool.schema.string(),
    options: tool.schema.array(tool.schema.string()),
  },
  async execute(args) {
    return JSON.stringify(await callTool("request_clarification", args))
  },
})
