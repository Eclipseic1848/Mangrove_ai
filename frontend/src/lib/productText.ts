/** 将内部 Agent 名称转换为面向用户的统一产品名称。 */
export function productText(value: string | null | undefined): string {
  return value?.replace(/\bPi\b/g, "Mangrove") ?? "";
}
