import { useEffect, useRef } from "react";

import type { EvidenceRef } from "@/types/dataPrep";

type Props = {
  url: string;
  name: string;
  evidence: EvidenceRef | null;
};

export function ImageEvidenceViewer({ url, name, evidence }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const element = host;
    let disposed = false;
    let viewer: import("openseadragon").Viewer | null = null;
    async function mountViewer() {
      const { default: OpenSeadragon } = await import("openseadragon");
      if (disposed) return;
      viewer = OpenSeadragon({
        element,
        tileSources: { type: "image", url },
        showNavigationControl: false,
        visibilityRatio: 0.5,
        minZoomImageRatio: 0.8,
        maxZoomPixelRatio: 4,
        gestureSettingsMouse: {
          clickToZoom: false,
          dblClickToZoom: true,
          scrollToZoom: true,
        },
      });
      viewer.addHandler("open", () => {
        const bbox = evidence?.bbox;
        const item = viewer?.world.getItemAt(0);
        if (!bbox || !item) return;
        const size = item.getContentSize();
        const scaleX = bbox.coordinate_space === "normalized_1000"
          ? size.x / 1000
          : 1;
        const scaleY = bbox.coordinate_space === "normalized_1000"
          ? size.y / 1000
          : 1;
        if (!["normalized_1000", "image_pixels"].includes(bbox.coordinate_space)) {
          return;
        }
        const overlay = document.createElement("div");
        overlay.setAttribute("aria-label", "图片证据高亮");
        overlay.className = "border-2 border-amber-500 bg-amber-300/25";
        viewer?.addOverlay({
          element: overlay,
          location: item.imageToViewportRectangle(
            bbox.x0 * scaleX,
            bbox.y0 * scaleY,
            (bbox.x1 - bbox.x0) * scaleX,
            (bbox.y1 - bbox.y0) * scaleY,
          ),
        });
      });
    }
    mountViewer();
    return () => {
      disposed = true;
      viewer?.destroy();
    };
  }, [url, evidence]);

  return (
    <div className="h-full min-h-[360px] w-full overflow-hidden rounded bg-slate-900">
      <div
        ref={hostRef}
        role="img"
        aria-label={`${name}可缩放预览`}
        className="h-full w-full"
      />
    </div>
  );
}
