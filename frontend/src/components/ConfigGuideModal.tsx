import { Modal } from "@/components/ui/modal";
import { CONFIG_GUIDE_SECTIONS, getGuideForKey, type GuideSection, type GuideStep } from "@/lib/configGuides";

/** 渲染一组有序步骤；每步可带一个跳转链接（如平台登录页/官网）。 */
function GuideStepsList({ steps }: { steps: GuideStep[] }) {
  return (
    <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
      {steps.map((step, i) => (
        <li key={i}>
          {step.text}
          {step.link && (
            <>
              {"："}
              <a
                href={step.link.url}
                target="_blank"
                rel="noreferrer"
                className="text-primary underline underline-offset-2"
              >
                {step.link.label}
              </a>
            </>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * 凭证配置指南面板：按分类展示怎么获取/怎么填。普通用户与管理员两处配置中心共用该组件，
 * 靠 sections 参数区分展示范围——不传则用普通用户的"模型 API Key / 平台 Cookie"两类，
 * 管理员配置中心传 ADMIN_GUIDE_SECTIONS 覆盖全部配置项。
 */
export function ConfigGuideModal(
  { open, onClose, sections = CONFIG_GUIDE_SECTIONS }: { open: boolean; onClose: () => void; sections?: GuideSection[] },
) {
  return (
    <Modal open={open} onClose={onClose} title="凭证配置指南" wide>
      <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
        {sections.map((section) => (
          <div key={section.key}>
            <h4 className="mb-2 text-sm font-semibold text-foreground">{section.title}</h4>
            <div className="space-y-3">
              {section.entries.map((entry) => (
                <div key={entry.key} className="rounded-md border border-border/60 p-3">
                  <div className="mb-1.5 text-sm font-medium">{entry.title}</div>
                  <GuideStepsList steps={entry.steps} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/** 编辑弹窗内嵌的精简步骤展示；该 key 没有对应指南时返回 null，不占位。 */
export function GuideStepsInline({ configKey }: { configKey: string }) {
  const entry = getGuideForKey(configKey);
  if (!entry) return null;
  return (
    <div className="mb-3 rounded-md border border-border/60 bg-muted/40 p-3">
      <GuideStepsList steps={entry.steps} />
    </div>
  );
}
