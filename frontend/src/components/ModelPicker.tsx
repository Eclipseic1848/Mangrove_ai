import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type ModelChoice = {
  connectionId: string; model: string; label: string; group: string; personal?: boolean;
};

type ModelPickerProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "value" | "onChange" | "children"> & {
  choices: ModelChoice[];
  value: string;
  onValueChange: (value: string) => void;
  emptyLabel: string;
  allowEmpty?: boolean;
  unavailableLabel?: string;
};

function SourceBadge({ personal, option = false }: { personal?: boolean; option?: boolean }) {
  return <Badge variant={personal ? "outline" : "default"}
    className={cn("shrink-0", personal ? "border-transparent bg-violet-100 text-violet-800" : "bg-primary/10 text-accent-foreground")}
    data-testid={option ? "model-option-source" : "model-picker-source"}>
    {personal ? "我的" : "平台"}
  </Badge>;
}

/** 原生顶层弹窗负责外点关闭；焦点留在选择按钮，选项不占用表单 Tab 顺序。 */
export function ModelPicker({ choices, value, onValueChange, emptyLabel, allowEmpty = false, unavailableLabel, className, disabled, ...props }: ModelPickerProps) {
  const generatedId = useId();
  const triggerId = props.id ?? `model-picker-${generatedId}`;
  const listId = `${triggerId}-options`;
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const selected = choices.find(item => JSON.stringify([item.connectionId, item.model]) === value);
  const groups = [false, true].flatMap(personal => {
    const source = choices.filter(item => Boolean(item.personal) === personal);
    return [...new Set(source.map(item => item.group))].map(group => ({
      label: `${personal ? "我的连接" : "平台提供"} · ${group}`,
      items: source.filter(item => item.group === group).map(choice => ({
        value: JSON.stringify([choice.connectionId, choice.model]), label: choice.label, disabled: false, choice,
      })),
    }));
  });
  const options: Array<{ value: string; label: string; disabled: boolean; choice?: ModelChoice }> = [
    { value: "", label: emptyLabel, disabled: !allowEmpty },
    ...(value && !selected && unavailableLabel ? [{ value, label: unavailableLabel, disabled: true }] : []),
    ...groups.flatMap(group => group.items),
  ];
  const enabled = options.map((item, index) => item.disabled ? -1 : index).filter(index => index >= 0);
  const optionId = (index: number) => `${listId}-${index}`;
  const close = () => { if (list.current?.matches(":popover-open")) list.current.hidePopover(); };

  function positionList() {
    if (!trigger.current || !list.current) return;
    const box = trigger.current.getBoundingClientRect();
    const width = Math.min(Math.max(box.width, 280), window.innerWidth - 16);
    const below = window.innerHeight - box.bottom - 8;
    const above = box.top - 8;
    const down = below >= Math.min(320, above);
    const height = Math.max(44, Math.min(320, down ? below : above));
    Object.assign(list.current.style, { width: `${width}px`, maxHeight: `${height}px`,
      left: `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`,
      top: `${down ? box.bottom + 4 : Math.max(8, box.top - Math.min(list.current.scrollHeight, height) - 4)}px` });
  }

  function show(index = options.findIndex(item => item.value === value && !item.disabled)) {
    if (disabled) return;
    setActive(index >= 0 ? index : enabled[0] ?? -1);
    typed.current = { text: "", at: 0 };
    list.current?.showPopover();
    positionList();
  }

  function choose(index: number) {
    if (!options[index] || options[index].disabled) return;
    onValueChange(options[index].value);
    close();
    trigger.current?.focus({ preventScroll: true });
  }

  useEffect(() => {
    const node = list.current;
    const changed = (event: Event) => setOpen((event as Event & { newState: string }).newState === "open");
    node?.addEventListener("beforetoggle", changed);
    return () => node?.removeEventListener("beforetoggle", changed);
  }, []);
  useEffect(() => { if (disabled) close(); }, [disabled]);
  useEffect(() => {
    if (!open) return;
    // 只滚动列表，不能让位于其下方的任务表单跟着跳动。
    const item = document.getElementById(optionId(active));
    if (item && list.current) {
      const itemBox = item.getBoundingClientRect(), listBox = list.current.getBoundingClientRect();
      if (itemBox.top < listBox.top) list.current.scrollTop -= listBox.top - itemBox.top;
      if (itemBox.bottom > listBox.bottom) list.current.scrollTop += itemBox.bottom - listBox.bottom;
    }
    window.addEventListener("resize", positionList);
    window.addEventListener("scroll", positionList, true);
    return () => { window.removeEventListener("resize", positionList); window.removeEventListener("scroll", positionList, true); };
  }, [open, active]);

  function keyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Escape" && open) {
      // 任务表单也监听 Escape；一次只关闭当前模型列表。
      event.preventDefault(); event.stopPropagation(); close(); return;
    }
    if (event.key === "Tab") { close(); return; }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open) show(event.key === "End" ? enabled[enabled.length - 1] : event.key === "Home" ? enabled[0] : undefined);
      else if (event.key === "Home") setActive(enabled[0] ?? -1);
      else if (event.key === "End") setActive(enabled[enabled.length - 1] ?? -1);
      else setActive(enabled[(enabled.indexOf(active) + (event.key === "ArrowDown" ? 1 : -1) + enabled.length) % enabled.length] ?? -1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); event.stopPropagation();
      if (open) choose(active); else show();
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      const now = Date.now(), text = open && now - typed.current.at < 700 ? typed.current.text + event.key : event.key;
      const prefix = (/^(.)\1+$/.test(text) ? event.key : text).toLocaleLowerCase();
      const start = prefix.length > 1 ? Math.max(active, 0) : active + 1;
      const index = options.map((_, offset) => (start + offset) % options.length)
        .find(index => !options[index].disabled && options[index].label.toLocaleLowerCase().startsWith(prefix));
      if (index !== undefined) { if (!open) show(index); else setActive(index); }
      typed.current = { text, at: now };
    }
  }

  function renderOption(item: typeof options[number]) {
    const index = options.indexOf(item);
    return <div key={item.value} id={optionId(index)} role="option" aria-selected={item.value === value}
      aria-disabled={item.disabled || undefined} data-value={item.value}
      onMouseDown={event => event.preventDefault()} onPointerMove={() => { if (!item.disabled) setActive(index); }} onClick={() => choose(index)}
      className={cn("flex min-h-11 items-center gap-2 rounded-md px-3 py-2 text-sm", item.disabled ? "text-muted-foreground" : "cursor-pointer", active === index && !item.disabled && "bg-accent text-accent-foreground")}>
      {item.choice && <SourceBadge personal={item.choice.personal} option />}
      <span className="min-w-0 flex-1 truncate" title={item.label} data-model-label={item.choice ? "" : undefined}>{item.label}</span>
      {item.value === value && <Check className="size-4 shrink-0" aria-hidden="true" />}
    </div>;
  }

  return <>
    <button {...props} ref={trigger} id={triggerId} type="button" role="combobox" value={value} disabled={disabled}
      aria-haspopup="listbox" aria-controls={listId} aria-expanded={open} aria-activedescendant={open && active >= 0 ? optionId(active) : undefined}
      onClick={() => { if (open) close(); else show(); }} onKeyDown={keyDown}
      className={cn("flex h-10 min-w-0 cursor-pointer items-center gap-2 rounded-lg border bg-background px-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60", className)}>
      {selected && <SourceBadge personal={selected.personal} />}
      <span className="min-w-0 flex-1 truncate">{selected?.label ?? (value && unavailableLabel ? unavailableLabel : emptyLabel)}</span>
      <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </button>
    <div ref={list} id={listId} {...{ popover: "auto" }} role="listbox" aria-label="可用模型" style={{ inset: "auto" }}
      className="fixed m-0 overflow-y-auto rounded-xl border bg-card p-1 text-card-foreground shadow-lg">
      {options.filter(item => !item.choice).map(renderOption)}
      {groups.map(group => <div key={group.label} role="group" aria-label={group.label}>
        <div className="px-3 pb-1 pt-3 text-xs text-muted-foreground" aria-hidden="true">{group.label}</div>
        {group.items.map(renderOption)}
      </div>)}
    </div>
  </>;
}
