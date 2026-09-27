import { useRef, useState, type CSSProperties } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Workbook = {
  sheets: string[]; total_rows: number; total_columns: number;
  cells: { text: string; style: CSSProperties }[][];
  widths: number[]; heights: number[];
  merges: { row: number; column: number; rows: number; columns: number }[];
  note: string;
};
function columnName(index: number): string {
  let result = "";
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26)) result = String.fromCharCode(65 + (n - 1) % 26) + result;
  return result;
}

export function WorkbookPreview({ uploadId }: { uploadId: string }) {
  const [sheet, setSheet] = useState(0);
  const [row, setRow] = useState(0);
  const [column, setColumn] = useState(0);
  const [pan, setPan] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null);
  const result = useQuery({
    queryKey: ["upload-workbook", uploadId, sheet, row, column],
    queryFn: (): Promise<Workbook> => api.get(`/api/data-sources/uploads/${encodeURIComponent(uploadId)}/workbook-preview?sheet=${sheet}&row=${row}&column=${column}`),
    retry: false,
  });
  const data = result.data;
  const button = "rounded border px-2 py-1.5 text-xs hover:bg-muted disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-ring";
  return <section aria-label="Excel 工作表预览" className="flex h-full min-h-0 min-w-0 flex-col gap-2">
    <div className="flex shrink-0 flex-wrap items-center gap-2">
      <button type="button" className={button} aria-pressed={pan} onClick={() => setPan(!pan)}>{pan ? "拖动查看" : "选择文字"}</button>
      <span className="text-xs text-muted-foreground">{pan ? "按住表格拖动；滚轮与滚动条仍可用" : "双击单元格或按 Enter 查看全文"}</span>
    </div>
    {result.isError ? <div role="alert" className="text-sm text-destructive">{result.error.message}<button type="button" className={button} onClick={() => void result.refetch()}>重试</button></div>
      : !data ? <p role="status">正在读取工作表…</p> : <>
      <div key={`${sheet}-${row}-${column}`} tabIndex={0} aria-label="工作表内容" className="min-h-0 flex-1 overflow-auto border bg-white text-slate-900 focus-visible:ring-2 focus-visible:ring-ring"
        style={{ cursor: pan ? "grab" : undefined, scrollbarGutter: "stable", userSelect: pan ? "none" : undefined }}
        onPointerDown={event => {
          if (!pan || event.button !== 0 || event.pointerType !== "mouse") return;
          const el = event.currentTarget, rect = el.getBoundingClientRect();
          if (event.clientX - rect.left - el.clientLeft >= el.clientWidth || event.clientY - rect.top - el.clientTop >= el.clientHeight) return;
          drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: el.scrollLeft, top: el.scrollTop };
          el.setPointerCapture(event.pointerId); event.preventDefault();
        }}
        onPointerMove={event => {
          const start = drag.current;
          if (!start || start.id !== event.pointerId) return;
          event.currentTarget.scrollLeft = start.left + start.x - event.clientX;
          event.currentTarget.scrollTop = start.top + start.y - event.clientY;
        }}
        onPointerUp={event => { drag.current = null; if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
        onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
        <table className="border-separate border-spacing-0 text-xs" style={{ tableLayout: "fixed", width: 48 + data.widths.reduce((sum, width) => sum + width, 0) }}>
          <colgroup><col style={{ width: 48 }} />{data.widths.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
          <thead className="sticky top-0 z-10 bg-slate-100"><tr><th className="border-b border-r p-2">#</th>{data.widths.map((_, index) => <th scope="col" key={index} className="border-b border-r p-2 font-normal">{columnName(column + index)}</th>)}</tr></thead>
          <tbody>{data.cells.map((cells, r) => <tr key={r} style={{ height: data.heights[r] }}>
            <th scope="row" className="sticky left-0 border-b border-r bg-slate-100 px-2 font-normal">{row + r + 1}</th>
            {cells.map((cell, c) => {
              const merged = data.merges.find(m => row+r >= m.row && row+r < m.row+m.rows && column+c >= m.column && column+c < m.column+m.columns);
              const top = merged ? Math.max(row, merged.row) : row+r;
              const left = merged ? Math.max(column, merged.column) : column+c;
              if (row+r !== top || column+c !== left) return null;
              const address = `${columnName(column+c)}${row+r+1}`;
              return <td key={c} tabIndex={0} aria-label={`${address} ${cell.text}`}
                rowSpan={merged ? Math.min(merged.row+merged.rows, row+data.cells.length)-top : 1}
                colSpan={merged ? Math.min(merged.column+merged.columns, column+data.widths.length)-left : 1}
                className="overflow-hidden border-b border-r px-2 py-1 align-top focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-600"
                style={cell.style} onDoubleClick={() => setDetail(`${address}\n${cell.text}`)}
                onKeyDown={event => { if (event.key === "Enter") setDetail(`${address}\n${cell.text}`); }}>
                <div className="whitespace-pre-wrap break-words">{cell.text}</div>
              </td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>
      {detail !== null && <div className="shrink-0 rounded border p-2 text-xs"><button type="button" className={button} onClick={() => setDetail(null)}>关闭单元格详情</button><pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words">{detail}</pre></div>}
      <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
        <label>工作表 <select aria-label="工作表" className="max-w-48 rounded border bg-background p-1.5" value={sheet} onChange={event => { setSheet(Number(event.target.value)); setRow(0); setColumn(0); setDetail(null); }}>
          {data.sheets.map((name, index) => <option key={index} value={index}>{name}</option>)}
        </select></label>
        <span>共 {data.total_rows} 行 × {data.total_columns} 列</span>
        <button type="button" className={button} disabled={row === 0} onClick={() => { setRow(Math.max(0, row-100)); setDetail(null); }}>上一段行</button>
        <button type="button" className={button} disabled={row+100 >= data.total_rows} onClick={() => { setRow(row+100); setDetail(null); }}>下一段行</button>
        <button type="button" className={button} disabled={column === 0} onClick={() => { setColumn(Math.max(0, column-30)); setDetail(null); }}>前一段列</button>
        <button type="button" className={button} disabled={column+30 >= data.total_columns} onClick={() => { setColumn(column+30); setDetail(null); }}>后一段列</button>
        <span>当前 {row+1}–{row+data.cells.length} 行 · {columnName(column)}–{columnName(column+data.widths.length-1)} 列</span>
      </div><p className="shrink-0 text-[11px] text-muted-foreground">{data.note}</p>
    </>}
  </section>;
}
